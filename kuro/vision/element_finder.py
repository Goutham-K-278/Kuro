from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class FoundElement:
    label: str
    center: Tuple[int, int]
    bbox: List[float]
    confidence: float
    method: str


class ElementFinder:
    def __init__(self):
        self._uia_available = False
        self._init_uia()

    def _init_uia(self) -> None:
        try:
            import uiautomation as auto

            self._auto = auto
            self._uia_available = True
            logger.info("[ElementFinder] UIA enabled")
        except Exception:
            logger.info("[ElementFinder] UIA unavailable, using vision only")

    def find(self, description: str, prefer_uia: bool = True, image=None) -> Optional[FoundElement]:
        if prefer_uia and self._uia_available:
            uia_result = self._find_via_uia(description)
            if uia_result:
                return uia_result

        from .florence_engine import FlorenceEngine
        from .screen_capture import ScreenCapture

        florence = FlorenceEngine.get_instance()
        if image is None:
            image = ScreenCapture().grab_for_florence(force=True)

        result = florence.find_element(image, description)
        if not result:
            return None

        return FoundElement(
            label=result.get("label", description),
            center=result["center"],
            bbox=result["bbox"],
            confidence=float(result.get("confidence", 0.8)),
            method=result.get("method", "grounding"),
        )

    def _find_via_uia(self, description: str) -> Optional[FoundElement]:
        if not self._uia_available:
            return None
        try:
            root = self._auto.GetForegroundControl()
            if not root:
                return None

            desc_lower = description.lower()
            for child in self._walk_uia(root, max_depth=6):
                name = (child.Name or "").lower()
                if desc_lower in name or name in desc_lower:
                    rect = child.BoundingRectangle
                    if rect.width() > 0 and rect.height() > 0:
                        cx = rect.left + rect.width() // 2
                        cy = rect.top + rect.height() // 2
                        return FoundElement(
                            label=child.Name or description,
                            center=(cx, cy),
                            bbox=[rect.left, rect.top, rect.right, rect.bottom],
                            confidence=0.95,
                            method="uia",
                        )
        except Exception as e:
            logger.debug("[ElementFinder] UIA lookup failed: %s", e)
        return None

    def _walk_uia(self, control, max_depth: int = 6, depth: int = 0):
        if depth > max_depth:
            return
        yield control
        try:
            child = control.GetFirstChildControl()
            while child:
                yield from self._walk_uia(child, max_depth, depth + 1)
                child = child.GetNextSiblingControl()
        except Exception:
            return
