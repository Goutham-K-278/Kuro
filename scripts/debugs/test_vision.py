"""Quick runtime diagnostic for Kuro vision pipeline."""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> None:
    print("=" * 60)
    print("KURO VISION DIAGNOSTIC")
    print("=" * 60)

    print("\n[1/4] Screen capture")
    from kuro.vision.screen_capture import ScreenCapture

    cap = ScreenCapture()
    t0 = time.perf_counter()
    img = cap.grab()
    ms = (time.perf_counter() - t0) * 1000
    print(f"  OK {img.size[0]}x{img.size[1]} in {ms:.1f}ms")

    print("\n[2/4] Florence load")
    from kuro.vision.florence_engine import FlorenceEngine

    engine = FlorenceEngine.get_instance()
    t0 = time.perf_counter()
    engine.ensure_loaded()
    ms = (time.perf_counter() - t0) * 1000
    print(f"  OK device={engine.device} load={ms:.0f}ms")

    print("\n[3/4] Caption")
    img_f = cap.grab_for_florence(force=True)
    t0 = time.perf_counter()
    caption = engine.caption(img_f)
    ms = (time.perf_counter() - t0) * 1000
    print(f"  OK {ms:.0f}ms -> {caption[:120]}")

    print("\n[4/4] OCR")
    t0 = time.perf_counter()
    ocr = engine.ocr_plain(img_f)
    ms = (time.perf_counter() - t0) * 1000
    print(f"  OK {ms:.0f}ms -> {ocr[:120]}")

    try:
        import torch

        if torch.cuda.is_available():
            used = torch.cuda.memory_allocated() // (1024**2)
            total = torch.cuda.get_device_properties(0).total_memory // (1024**2)
            print(f"\nVRAM: used={used}MB total={total}MB free={total-used}MB")
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug(f"Non-critical error: {e}")

    print("\nDiagnostic complete.")


if __name__ == "__main__":
    main()
