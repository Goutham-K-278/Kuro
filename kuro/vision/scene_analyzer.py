from __future__ import annotations

import ctypes
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from PIL import Image

logger = logging.getLogger(__name__)


@dataclass
class ScreenState:
    description: str = ""
    active_app: str = ""
    active_window_title: str = ""
    visible_text: str = ""
    elements: List[Dict[str, Any]] = field(default_factory=list)
    app_state: str = ""
    timestamp: float = 0.0
    analysis_ms: float = 0.0

    def to_llm_context(self) -> str:
        lines = [
            "[KURO VISION - Current Screen State]",
            f"Active App: {self.active_app or 'Unknown'}",
            f"Window: {self.active_window_title or 'Unknown'}",
            f"Description: {self.description}",
        ]
        if self.app_state:
            lines.append(f"App State: {self.app_state}")
        if self.visible_text:
            text = self.visible_text[:500] + ("..." if len(self.visible_text) > 500 else "")
            lines.append(f"Visible Text: {text}")
        if self.elements:
            lines.append("Key Elements:")
            for e in self.elements[:10]:
                label = e.get("label") or e.get("text") or e.get("caption", "?")
                lines.append(f"  - {label}")
        lines.append(f"[Captured {self.analysis_ms:.0f}ms ago]")
        return "\n".join(lines)


class SceneAnalyzer:
    def __init__(self):
        from .florence_engine import FlorenceEngine
        from .screen_capture import ScreenCapture

        self._capture = ScreenCapture()
        self._florence = FlorenceEngine.get_instance()
        self._last_state: Optional[ScreenState] = None

    def analyze(self, image: Optional[Image.Image] = None, quick: bool = False) -> ScreenState:
        t0 = time.perf_counter()
        if image is None:
            image = self._capture.grab_for_florence(force=True)

        active_app, title = self._get_active_window()

        if quick:
            caption = self._florence.caption(image, detail="brief")
            ocr_text = ""
            elements: List[Dict[str, Any]] = []
        else:
            data = self._florence.analyze(image)
            caption = data["caption"]
            ocr_text = data["ocr_text"]
            elements = data["ocr_elements"]

        app_state = self._detect_app_state(active_app, ocr_text, caption)
        elapsed = (time.perf_counter() - t0) * 1000

        state = ScreenState(
            description=caption,
            active_app=active_app,
            active_window_title=title,
            visible_text=ocr_text,
            elements=elements,
            app_state=app_state,
            timestamp=time.time(),
            analysis_ms=elapsed,
        )
        self._last_state = state
        logger.info("[SceneAnalyzer] %.0fms app=%s state=%s", elapsed, active_app, app_state)
        return state

    def quick_check(self) -> ScreenState:
        return self.analyze(quick=True)

    def find_element(self, description: str) -> Optional[Dict[str, Any]]:
        image = self._capture.grab_for_florence(force=True)
        return self._florence.find_element(image, description)

    @property
    def last_state(self) -> Optional[ScreenState]:
        return self._last_state

    def _get_active_window(self) -> tuple[str, str]:
        try:
            import pygetwindow as gw

            active = gw.getActiveWindow()
            if active:
                title = active.title or ""
                return self._title_to_app_name(title), title
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug(f"Non-critical error: {e}")

        try:
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd) + 1
            buf = ctypes.create_unicode_buffer(length)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, length)
            title = buf.value
            return self._title_to_app_name(title), title
        except Exception:
            return "unknown", ""

    @staticmethod
    def _title_to_app_name(title: str) -> str:
        t = title.lower()
        patterns = {
            "whatsapp": "whatsapp",
            "telegram": "telegram",
            "discord": "discord",
            "chrome": "chrome",
            "spotify": "spotify",
            "visual studio code": "vscode",
            "code -": "vscode",
            "notepad": "notepad",
            "file explorer": "explorer",
            "powershell": "powershell",
            "terminal": "terminal",
        }
        for pattern, app in patterns.items():
            if pattern in t:
                return app
        return title.split(" - ")[-1].strip().lower() if " - " in title else t

    @staticmethod
    def _detect_app_state(app_name: str, ocr_text: str, caption: str) -> str:
        ocr = ocr_text.lower()
        cap = caption.lower()

        if app_name == "whatsapp":
            if "type a message" in ocr or "compose" in ocr:
                return "whatsapp:in_chat"
            if "search" in ocr and ("contact" in ocr or "chat" in ocr):
                return "whatsapp:searching"
            if "chats" in ocr or "chat list" in cap:
                return "whatsapp:chat_list"
            return "whatsapp:open"

        if app_name == "spotify":
            if "playing" in cap or "pause" in ocr:
                return "spotify:playing"
            if "search" in ocr:
                return "spotify:searching"
            return "spotify:open"

        if app_name == "chrome":
            if "youtube" in ocr:
                return "chrome:youtube"
            if "google" in ocr:
                return "chrome:google"
            return "chrome:open"

        return f"{app_name}:open"
