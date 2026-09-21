from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from PIL import Image

logger = logging.getLogger(__name__)


@dataclass
class VerificationResult:
    success: bool = False
    changed: bool = False
    expected_change: bool = False
    description: str = ""
    pre_state: str = ""
    post_state: str = ""
    error: str = ""
    suggestion: str = ""
    verify_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "changed": self.changed,
            "expected_change": self.expected_change,
            "description": self.description,
            "error": self.error,
            "suggestion": self.suggestion,
            "verify_ms": self.verify_ms,
        }


class ActionVerifier:
    def __init__(self):
        from .florence_engine import FlorenceEngine
        from .screen_capture import ScreenCapture

        self._capture = ScreenCapture()
        self._florence = FlorenceEngine.get_instance()

    def capture_pre_state(self) -> Dict[str, Any]:
        image = self._capture.grab_for_florence(force=True)
        return {
            "image": image,
            "caption": self._florence.caption(image, detail="brief"),
            "ocr_text": self._florence.ocr_plain(image),
            "timestamp": time.time(),
        }

    def verify(
        self,
        pre_state: Dict[str, Any],
        expected: str = "",
        expected_text: str = "",
        expected_gone: str = "",
        timeout: float = 2.0,
        poll_interval: float = 0.3,
    ) -> VerificationResult:
        t0 = time.perf_counter()
        deadline = t0 + timeout

        pre_caption = pre_state.get("caption", "")
        pre_ocr = pre_state.get("ocr_text", "")

        changed = False
        expected_found = False
        post_ocr = pre_ocr
        post_caption = pre_caption

        while time.perf_counter() < deadline:
            image = self._capture.grab_for_florence(force=True)
            post_ocr = self._florence.ocr_plain(image)

            if post_ocr != pre_ocr:
                changed = True

            if expected_text and expected_text.lower() in post_ocr.lower():
                expected_found = True
                post_caption = self._florence.caption(image, detail="brief")
                break

            if expected_gone and expected_gone.lower() not in post_ocr.lower():
                expected_found = True
                post_caption = self._florence.caption(image, detail="brief")
                break

            if not expected_text and not expected_gone and changed:
                expected_found = True
                post_caption = self._florence.caption(image, detail="brief")
                break

            time.sleep(poll_interval)

        elapsed = (time.perf_counter() - t0) * 1000

        if expected_found:
            return VerificationResult(
                success=True,
                changed=True,
                expected_change=True,
                description="Action verified: screen changed as expected",
                pre_state=pre_caption,
                post_state=post_caption,
                verify_ms=elapsed,
            )

        if changed:
            return VerificationResult(
                success=False,
                changed=True,
                expected_change=False,
                description="Screen changed but not as expected",
                pre_state=pre_caption,
                post_state=post_caption,
                error=f"Expected '{expected_text or expected}' but saw different content",
                suggestion="Check if wrong element was clicked or popup interrupted flow",
                verify_ms=elapsed,
            )

        return VerificationResult(
            success=False,
            changed=False,
            expected_change=False,
            description="Screen did not change after action",
            pre_state=pre_caption,
            post_state=pre_caption,
            error="Action had no visible effect",
            suggestion="Retry with a different target or app focus",
            verify_ms=elapsed,
        )

    def detect_popup(self, image: Optional[Image.Image] = None) -> Optional[Dict[str, Any]]:
        if image is None:
            image = self._capture.grab_for_florence(force=True)

        caption = self._florence.caption(image, detail="brief").lower()
        indicators = [
            "dialog", "popup", "alert", "notification", "error", "warning",
            "permission", "allow", "deny", "confirm",
        ]

        if not any(w in caption for w in indicators):
            return None

        ocr = self._florence.ocr_plain(image)
        button_words = [
            "ok", "cancel", "yes", "no", "allow", "deny", "close",
            "accept", "decline", "retry", "continue", "dismiss",
        ]

        return {
            "type": "dialog",
            "description": caption,
            "text": ocr[:200],
            "buttons": [w for w in button_words if w in ocr.lower()],
        }
