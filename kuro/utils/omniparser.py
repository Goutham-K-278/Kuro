"""
kuro/utils/omniparser.py
─────────────────────────────────────────────────────────────────
Visual Grounding via OmniParser V2

OmniParser runs as a SEPARATE local microservice (port 8001 by default).
Set up once with:  python setup_models.py

On each call it:
  1. Captures a screenshot with MSS (~5ms)
  2. Sends it to the OmniParser HTTP endpoint
  3. Returns a list of UIElement objects with bounding boxes + labels

If OmniParser is not running, all methods return empty results
(non-blocking fallback — agents still work without it).
─────────────────────────────────────────────────────────────────
"""

import base64
import io
import logging
import os
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

_HOST = os.getenv("OMNIPARSER_HOST", "127.0.0.1")
_PORT = os.getenv("OMNIPARSER_PORT", "8001")
OMNIPARSER_URL = f"http://{_HOST}:{_PORT}"


@dataclass
class UIElement:
    """A detected interactable UI element on screen."""
    label:        str
    bbox:         tuple   # (x1, y1, x2, y2) in screen-pixel coordinates
    element_type: str     # "icon" | "text" | "button"
    confidence:   float

    @property
    def center(self) -> tuple:
        """Center pixel for clicking."""
        x1, y1, x2, y2 = self.bbox
        return ((x1 + x2) // 2, (y1 + y2) // 2)


class OmniParserClient:
    """
    Async client for the OmniParser V2 microservice.
    When OmniParser is offline, all methods silently return [] / None.
    """

    def __init__(self):
        import mss
        self._http = None
        self._sct  = mss.mss()
        logger.info(f"[OmniParser] Client ready (lazy HTTP) -> {OMNIPARSER_URL}")

    async def _get_client(self):
        import httpx
        if self._http is None or getattr(self._http, "is_closed", True):
            self._http = httpx.AsyncClient(
                timeout=10.0,
                limits=httpx.Limits(max_connections=4)
            )
        return self._http

    async def parse_screen(
        self,
        region: Optional[dict] = None,
        target_label: Optional[str] = None,
    ) -> list[UIElement]:
        """
        Capture a screenshot, send to OmniParser, return UIElements.

        Args:
            region:       {"top": y, "left": x, "width": w, "height": h}
                          None = full primary screen
            target_label: optional filter — only return elements whose label
                          contains this substring (case-insensitive)
        """
        import httpx
        from PIL import Image

        # ── Screenshot capture (~5ms with MSS) ───────────────────────────────
        monitor    = region or self._sct.monitors[1]
        screenshot = self._sct.grab(monitor)

        img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        img_b64 = base64.b64encode(buf.getvalue()).decode()

        # ── HTTP call to OmniParser V2 ────────────────────────────────────────
        client = await self._get_client()
        try:
            resp = await client.post(
                f"{OMNIPARSER_URL}/parse",
                json={
                    "image_base64":             img_b64,
                    "caption_model_patch_size": 14,    # V2 default patch size
                    "box_threshold":            0.05,
                },
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.ConnectError:
            logger.debug("OmniParser microservice not running — using coordinate fallback")
            return []
        except Exception as e:
            logger.warning(f"[OmniParser] Request error: {e}")
            return []

        # ── Parse results ─────────────────────────────────────────────────────
        w, h      = img.size
        elements  = []

        for item in data.get("parsed_content_list", []):
            bbox_n = item.get("bbox", [0, 0, 0, 0])   # normalised [0, 1]
            bbox_px = (
                int(bbox_n[0] * w),
                int(bbox_n[1] * h),
                int(bbox_n[2] * w),
                int(bbox_n[3] * h),
            )
            # Offset for sub-region grabs
            if region:
                ox, oy = region.get("left", 0), region.get("top", 0)
                bbox_px = (bbox_px[0] + ox, bbox_px[1] + oy,
                           bbox_px[2] + ox, bbox_px[3] + oy)

            elements.append(UIElement(
                label        = item.get("content", ""),
                bbox         = bbox_px,
                element_type = item.get("type", "icon"),
                confidence   = float(item.get("interactivity", 0.5)),
            ))

        logger.debug(f"[OmniParser] {len(elements)} elements found")

        if target_label:
            lo = target_label.lower()
            elements = [e for e in elements if lo in e.label.lower()]
            logger.debug(f"[OmniParser] After filter '{target_label}': {len(elements)} elements")

        return elements

    async def find_and_verify(
        self,
        target_label: str,
        max_attempts: int = 3,
    ) -> Optional[UIElement]:
        """
        Observation loop: take screenshots and look for target_label.
        Retries up to max_attempts times. Returns best match or None.
        """
        for attempt in range(1, max_attempts + 1):
            elements = await self.parse_screen(target_label=target_label)
            if elements:
                best = max(elements, key=lambda e: e.confidence)
                logger.info(
                    f"[OmniParser] Found '{target_label}' at {best.center} "
                    f"(attempt {attempt})"
                )
                return best
            logger.debug(
                f"[OmniParser] '{target_label}' not found (attempt {attempt}/{max_attempts})"
            )

        return None

    async def close(self) -> None:
        self._sct.close()
        if self._http and not getattr(self._http, "is_closed", True):
            await self._http.aclose()
