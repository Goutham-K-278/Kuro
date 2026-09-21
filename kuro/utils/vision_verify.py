"""
kuro/utils/vision_verify.py
─────────────────────────────────────────────────────────────────
Vision Verification via LLaVA (Ollama local) or Groq Vision

Gives Kuro EYES — takes a screenshot and asks a vision LLM what's
on screen. Used by agents to verify actions succeeded.

Architecture:
  1. Capture screenshot via MSS (~5ms)
  2. Send base64 PNG + question to vision LLM
  3. Return the LLM's text answer

Vision backends:
  - "local"  (default): llava:7b via Ollama
  - "groq":  Groq multimodal API (if available)
─────────────────────────────────────────────────────────────────
"""

import asyncio
import base64
import io
import logging
import os
import re
from typing import Optional

logger = logging.getLogger(__name__)


class VisionVerifier:
    """
    Lightweight vision helper — screenshot + question → answer.
    Uses llava:7b via Ollama (local, ~3-5s per query).
    """

    def __init__(self):
        self._ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        self._vision_model = os.getenv("OLLAMA_VISION_MODEL", "llava:7b")
        self._available = False
        self._mss_available = False

        # Check if vision model exists
        self._check_availability()

        # Verify screen capture dependency; capture objects are created per call
        # to avoid thread-local MSS handle issues during background verification.
        try:
            import mss
            _ = mss  # keep import for availability check
            self._mss_available = True
        except ImportError:
            logger.warning("[Vision] mss not installed — screen capture disabled")

        logger.info(f"[Vision] VisionVerifier ready — model: {self._vision_model}, "
                     f"available: {self._available}")

    def _check_availability(self):
        """Check if the vision model is available in Ollama."""
        try:
            import httpx
            r = httpx.get(f"{self._ollama_url}/api/tags", timeout=3.0)
            models = [m["name"] for m in r.json().get("models", [])]
            self._available = any(self._vision_model in m for m in models)
            if self._available:
                logger.info(f"[Vision] Found vision model: {self._vision_model}")
            else:
                logger.warning(f"[Vision] Vision model '{self._vision_model}' not found in Ollama. "
                              f"Available: {models}")
        except Exception as e:
            logger.warning(f"[Vision] Ollama not reachable: {e}")

    # ── Screenshot ────────────────────────────────────────────────────────────

    def _capture_screenshot_b64(self) -> str:
        """Capture primary monitor screenshot as base64 PNG."""
        if not self._mss_available:
            raise RuntimeError("mss not available for screen capture")

        import mss
        import mss.tools
        with mss.mss() as sct:
            monitor = sct.monitors[1]  # primary monitor
            shot = sct.grab(monitor)
        png_bytes = mss.tools.to_png(shot.rgb, shot.size)
        return base64.b64encode(png_bytes).decode("utf-8")

    # ── Core: Ask Vision ──────────────────────────────────────────────────────

    async def ask_vision(self, question: str, max_tokens: int = 256) -> str:
        """
        Screenshot the screen, ask a vision LLM a question about it.

        Args:
            question: Natural language question about the screen state
            max_tokens: Max response length

        Returns:
            The vision LLM's text answer, or empty string if unavailable.
        """
        if not self._available:
            logger.debug("[Vision] Vision model not available — skipping")
            return ""

        try:
            screenshot_b64 = await asyncio.get_event_loop().run_in_executor(
                None, self._capture_screenshot_b64
            )
        except Exception as e:
            logger.warning(f"[Vision] Screenshot failed: {e}")
            return ""

        try:
            import httpx
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{self._ollama_url}/api/generate",
                    json={
                        "model": self._vision_model,
                        "prompt": question,
                        "images": [screenshot_b64],
                        "stream": False,
                        "options": {
                            "temperature": 0,
                            "num_predict": max_tokens,
                        },
                    },
                )
                resp.raise_for_status()
                answer = resp.json().get("response", "").strip()
                logger.info(f"[Vision] Q: '{question[:50]}' → A: '{answer[:80]}'")
                return answer

        except Exception as e:
            logger.error(f"[Vision] LLaVA call failed: {e}")
            return ""

    # ── WhatsApp-Specific Verifications ───────────────────────────────────────

    async def verify_whatsapp_contact(self, expected_contact: str) -> dict:
        """
        After searching in WhatsApp, verify the correct contact appeared.

        Returns:
            {"found": bool, "description": str}
        """
        question = (
            f"Look at the WhatsApp window on screen. I searched for a contact named '{expected_contact}'. "
            f"Is there a chat or contact matching '{expected_contact}' visible in the search results or chat list? "
            f"Answer with YES or NO first, then briefly describe what you see."
        )
        answer = await self.ask_vision(question)
        if not answer:
            return {"found": True, "description": "Vision unavailable — proceeding optimistically"}

        found = bool(re.match(r"^\s*yes\b", answer, re.I))
        return {"found": found, "description": answer[:200]}

    async def verify_message_sent(self, message: str) -> dict:
        """
        After sending a message, verify it appeared in the chat.

        Returns:
            {"sent": bool, "description": str}
        """
        question = (
            f"Look at the WhatsApp chat window on screen. "
            f"Has the message '{message}' been sent successfully? "
            f"Look for a sent message bubble (on the right side) containing this text. "
            f"Answer with YES or NO first, then briefly describe what you see."
        )
        answer = await self.ask_vision(question)
        if not answer:
            return {"sent": True, "description": "Vision unavailable — assuming success"}

        sent = bool(re.match(r"^\s*yes\b", answer, re.I))
        return {"sent": sent, "description": answer[:200]}

    async def describe_screen(self) -> str:
        """Get a general description of what's currently on screen."""
        return await self.ask_vision(
            "Describe what application is currently visible on screen and what state it's in. "
            "Be concise — 2-3 sentences max."
        )

    async def check_whatsapp_state(self) -> dict:
        """
        Check the current state of WhatsApp on screen.

        Returns:
            {"open": bool, "state": str, "description": str}
        """
        question = (
            "Is WhatsApp currently visible on screen? "
            "If yes, describe its state: is it showing the chat list, a specific chat, "
            "or the search results? Answer with YES or NO first, then describe."
        )
        answer = await self.ask_vision(question)
        if not answer:
            return {"open": False, "state": "unknown", "description": "Vision unavailable"}

        is_open = bool(re.match(r"^\s*yes\b", answer, re.I))
        return {"open": is_open, "state": "visible" if is_open else "not_visible",
                "description": answer[:200]}

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def close(self):
        # No persistent capture handle to close.
        return


# ── Singleton ────────────────────────────────────────────────────────────────

_instance: Optional[VisionVerifier] = None


def get_vision_verifier() -> VisionVerifier:
    """Return the module-level VisionVerifier singleton."""
    global _instance
    if _instance is None:
        _instance = VisionVerifier()
    return _instance
