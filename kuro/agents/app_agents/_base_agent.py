from abc import ABC
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

class BaseAppAgent(ABC):
    """Base class for all app agents. Implements vision fallback."""
    
    def __init__(self):
        self._vision = None
    
    def _get_vision(self):
        if self._vision is None:
            from kuro.vision.florence_engine import FlorenceEngine
            self._vision = FlorenceEngine()
        return self._vision
    
    async def vision_find_and_click(self, description: str) -> bool:
        """
        Find a UI element by text/description using Florence-2 and click it.
        Use this as fallback when UIA/keyboard methods fail.
        """
        from kuro.vision.screen_capture import capture_screen
        screen = capture_screen()
        vision = self._get_vision()
        
        try:
            result = await vision.phrase_grounding(screen, description)
            if not result or not result.get("boxes"):
                logger.warning(f"Vision: could not find '{description}' on screen")
                return False
            
            box = result["boxes"][0]  # [x1_norm, y1_norm, x2_norm, y2_norm]
            h, w = screen.shape[:2]
            cx = int((box[0] + box[2]) / 2 * w)
            cy = int((box[1] + box[3]) / 2 * h)
            
            import pyautogui
            pyautogui.click(cx, cy)
            logger.info(f"Vision click: '{description}' at ({cx}, {cy})")
            return True
            
        except Exception as e:
            logger.error(f"Vision fallback error: {e}")
            return False
    
    async def vision_find_text(self, text: str) -> Optional[Tuple[int, int]]:
        """Returns (x, y) center of text found on screen, or None."""
        from kuro.vision.screen_capture import capture_screen
        screen = capture_screen()
        result = await self._get_vision().ocr(screen)
        
        # Search OCR result for matching text
        if not result or not result.get("text_regions"):
            return None
            
        for region in result["text_regions"]:
            if text.lower() in region.get("text", "").lower():
                box = region["box"]
                h, w = screen.shape[:2]
                cx = int((box[0] + box[2]) / 2 * w)
                cy = int((box[1] + box[3]) / 2 * h)
                return (cx, cy)
                
        return None
