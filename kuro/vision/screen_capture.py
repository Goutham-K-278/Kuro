from __future__ import annotations

import logging
import threading
import time
from typing import Optional, Tuple

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)
_CACHE_TTL_S = 0.5


class ScreenCapture:
    _instance: Optional["ScreenCapture"] = None
    _singleton_lock = threading.Lock()

    def __new__(cls) -> "ScreenCapture":
        if cls._instance is None:
            with cls._singleton_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True

        import mss

        self._sct = mss.mss()
        self._primary = self._sct.monitors[1]
        self.width = int(self._primary["width"])
        self.height = int(self._primary["height"])

        self._cache_img: Optional[Image.Image] = None
        self._cache_time = 0.0
        self._cache_lock = threading.Lock()

        logger.info("[ScreenCapture] Ready - %sx%s", self.width, self.height)

    def grab(self, force: bool = False) -> Image.Image:
        now = time.monotonic()
        with self._cache_lock:
            if not force and self._cache_img is not None and now - self._cache_time < _CACHE_TTL_S:
                return self._cache_img

        raw = self._sct.grab(self._primary)
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

        with self._cache_lock:
            self._cache_img = img
            self._cache_time = now
        return img

    def grab_region(self, left: int, top: int, right: int, bottom: int) -> Image.Image:
        monitor = {
            "left": max(0, int(left)),
            "top": max(0, int(top)),
            "width": max(1, min(int(right - left), self.width - int(left))),
            "height": max(1, min(int(bottom - top), self.height - int(top))),
        }
        raw = self._sct.grab(monitor)
        return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

    def grab_for_florence(self, force: bool = False) -> Image.Image:
        return self._resize_with_padding(self.grab(force=force), 768, 768)

    def grab_numpy(self, force: bool = False) -> np.ndarray:
        return np.asarray(self.grab(force=force), dtype=np.uint8)

    def invalidate_cache(self) -> None:
        with self._cache_lock:
            self._cache_img = None
            self._cache_time = 0.0

    @staticmethod
    def _resize_with_padding(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
        w, h = img.size
        scale = min(target_w / w, target_h / h)
        new_w = int(w * scale)
        new_h = int(h * scale)
        resized = img.resize((new_w, new_h), Image.LANCZOS)
        canvas = Image.new("RGB", (target_w, target_h), (0, 0, 0))
        canvas.paste(resized, ((target_w - new_w) // 2, (target_h - new_h) // 2))
        return canvas

    @property
    def screen_size(self) -> Tuple[int, int]:
        return (self.width, self.height)
