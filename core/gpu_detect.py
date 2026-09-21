"""
core/gpu_detect.py
──────────────────────────────────────────────────────────────
One-time GPU detection at startup.
Detects NVIDIA CUDA and sets system-wide GPU availability flag.

All Kuro components (STT, TTS, Ollama, Vision) read GPU_AVAILABLE
from here instead of each doing their own detection.

Usage:
    from core.gpu_detect import GPU_AVAILABLE, GPU_NAME, GPU_VRAM_MB, effective_device

    device = effective_device("cuda")   # returns "cuda" or "cpu"
──────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _detect() -> dict:
    """Run GPU detection once and cache the result for the lifetime of the process."""
    result = {
        "available": False,
        "name": "No GPU",
        "vram_total_mb": 0,
        "vram_free_mb": 0,
        "driver_version": "",
    }

    # 1. Check env override first — user can force CPU via GPU_DISABLED=true in .env
    if os.getenv("GPU_DISABLED", "false").lower() == "true":
        logger.info("[GPU] GPU_DISABLED=true — forcing CPU mode")
        return result

    # 2. Try torch.cuda (most reliable for NVIDIA)
    try:
        import torch
        if torch.cuda.is_available():
            idx = 0
            props = torch.cuda.get_device_properties(idx)
            total_mb = props.total_memory // (1024 * 1024)
            free_mb = torch.cuda.mem_get_info(idx)[0] // (1024 * 1024)
            result.update({
                "available": True,
                "name": props.name,
                "vram_total_mb": total_mb,
                "vram_free_mb": free_mb,
                "driver_version": str(torch.version.cuda or ""),
            })
            logger.info(
                f"[GPU] ✅ Detected: {props.name} | "
                f"VRAM {free_mb}MB free / {total_mb}MB total | "
                f"CUDA {torch.version.cuda}"
            )
            return result
    except Exception as e:
        logger.debug(f"[GPU] torch check failed: {e}")

    # 3. Fallback: try ctypes / nvml if torch not installed
    try:
        import ctypes
        nvml = ctypes.CDLL("nvml.dll" if os.name == "nt" else "libnvidia-ml.so.1")
        if nvml.nvmlInit() == 0:
            result["available"] = True
            result["name"] = "NVIDIA GPU (nvml)"
            logger.info("[GPU] ✅ NVIDIA GPU detected via nvml")
            nvml.nvmlShutdown()
            return result
    except Exception:
        pass

    logger.info("[GPU] ❌ No CUDA GPU detected — running in CPU-only mode")
    return result


# ── Module-level constants (computed once on import) ──────────────────────────
_info = _detect()

GPU_AVAILABLE: bool = _info["available"]
GPU_NAME: str = _info["name"]
GPU_VRAM_TOTAL_MB: int = _info["vram_total_mb"]
GPU_VRAM_FREE_MB: int = _info["vram_free_mb"]
GPU_DRIVER_VERSION: str = _info["driver_version"]


def effective_device(preferred: str = "cuda") -> str:
    """
    Return 'cuda' if GPU is available and preferred, else 'cpu'.

    Args:
        preferred: Device the component would like to use.
                   If 'cuda' but GPU unavailable, returns 'cpu'.
                   If 'cpu', always returns 'cpu'.
    Returns:
        'cuda' or 'cpu'
    """
    if preferred == "cpu":
        return "cpu"
    return "cuda" if GPU_AVAILABLE else "cpu"


def compute_type_for_device(device: str) -> str:
    """
    Return the optimal faster-whisper compute_type for the given device.

    CUDA  → int8_float16  (fastest on RTX GPUs, half the VRAM of fp16)
    CPU   → int8          (fastest on CPU, no GPU required)
    """
    return "int8_float16" if device == "cuda" else "int8"


def ollama_gpu_layers() -> int:
    """
    Return how many Ollama GPU layers to use.
    0 = CPU only, -1 = all layers on GPU (Ollama default = auto).
    """
    if not GPU_AVAILABLE:
        return 0
    # With 6GB VRAM and ~3.5GB budget for LLM, use all layers on GPU
    return -1  # let Ollama decide automatically


def status_line() -> str:
    """Human-readable one-liner for startup logs."""
    if GPU_AVAILABLE:
        return (
            f"GPU: {GPU_NAME} | "
            f"VRAM {GPU_VRAM_FREE_MB}MB free / {GPU_VRAM_TOTAL_MB}MB total"
        )
    return "GPU: None — CPU-only mode"
