"""
KURO — VRAM-Safe Model Manager (Phase 8 rewrite)
==================================================
Key improvements over the original:

1. GPU LOADING GATE  — asyncio.Semaphore(1) ensures only ONE heavy model
   loads at a time, preventing the race condition that caused OOM crashes
   when Whisper + Kokoro both initialised simultaneously.

2. REAL VRAM UNLOAD  — del + torch.cuda.empty_cache() + gc.collect()
   actually frees GPU memory instead of just dropping the Python reference.

3. VRAM BUDGET CHECK — refuses to load a model if free VRAM < 600 MB,
   routing the call to Groq/CPU before even attempting GPU allocation.

4. NON-BLOCKING LOAD — heavy loaders run in run_in_executor so they
   never stall the FastAPI event loop while a model warms up.
"""
from __future__ import annotations

import asyncio
import gc
import logging
import subprocess
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ── VRAM threshold below which we refuse to load a GPU model ─────────────────
_VRAM_FLOOR_MB = 600   # If < 600 MB free, route to Groq / CPU instead


def _get_free_vram_mb() -> int:
    """Read free VRAM via nvidia-smi. Returns 9999 if GPU unavailable."""
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            timeout=1,
        ).decode().strip()
        return int(out)
    except Exception:
        return 9999


def _torch_empty_cache() -> None:
    """Free GPU memory caches if PyTorch is available."""
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug(f"Non-critical error: {e}")


class LazyModelManager:
    """
    Models load on first use, unload after TTL of no use.

    Thread-safety contract
    ──────────────────────
    • _gpu_gate (Semaphore): only ONE model loads on GPU at a time.
      Prevents the OOM race between Whisper + Kokoro at startup.
    • Unloading is synchronous (del + cache clear) — safe because it only
      happens during the GC loop, not mid-inference.
    """

    def __init__(self, ttl_seconds: int = 300):
        self._models:  dict[str, tuple[Any, float]] = {}   # name → (model, last_used_ts)
        self._loaders: dict[str, Callable]           = {}   # name → factory fn
        self._ttl      = ttl_seconds
        self._gpu_gate = asyncio.Semaphore(1)               # serialises GPU loads

    # ── Registration ──────────────────────────────────────────────────────────

    def register(self, name: str, loader: Callable) -> None:
        """Register a model factory. Called at startup, before any loading."""
        self._loaders[name] = loader

    # ── Sync accessor (call from inside run_in_executor only) ─────────────────

    def get_sync(self, name: str) -> Any:
        """
        Synchronous model get — safe ONLY when called from a thread pool
        (i.e. inside loop.run_in_executor).  Loads if not in cache.
        VRAM budget is checked; raises RuntimeError if GPU is full.
        """
        if name in self._models:
            model, _ = self._models[name]
            self._models[name] = (model, time.time())
            return model

        free_mb = _get_free_vram_mb()
        if free_mb < _VRAM_FLOOR_MB:
            raise RuntimeError(
                f"[ModelManager] VRAM too low ({free_mb} MB free < {_VRAM_FLOOR_MB} MB floor). "
                f"Cannot load '{name}' on GPU — route to Groq/CPU."
            )

        logger.info(f"[ModelManager] Loading '{name}' (free VRAM: {free_mb} MB)…")
        model = self._loaders[name]()
        self._models[name] = (model, time.time())
        logger.info(f"[ModelManager] '{name}' ready.")
        return model

    # ── Async accessor (main API — call from coroutines) ──────────────────────

    async def get(self, name: str) -> Any:
        """
        Async model get — event-loop safe.
        Heavy loader runs in the thread pool; only one GPU load at a time
        thanks to _gpu_gate.
        """
        if name in self._models:
            model, _ = self._models[name]
            self._models[name] = (model, time.time())
            return model

        # Check VRAM before acquiring the gate (fast path rejection)
        free_mb = _get_free_vram_mb()
        if free_mb < _VRAM_FLOOR_MB:
            raise RuntimeError(
                f"[ModelManager] VRAM too low ({free_mb} MB free). "
                f"Cannot load '{name}'."
            )

        async with self._gpu_gate:
            # Double-check: another coroutine may have loaded it while we waited
            if name in self._models:
                model, _ = self._models[name]
                self._models[name] = (model, time.time())
                return model

            logger.info(f"[ModelManager] Loading '{name}' (free VRAM: {free_mb} MB)…")
            loop = asyncio.get_event_loop()
            model = await loop.run_in_executor(None, self._loaders[name])
            self._models[name] = (model, time.time())
            logger.info(f"[ModelManager] '{name}' ready.")
            return model

    # ── Unload ────────────────────────────────────────────────────────────────

    def unload(self, name: str) -> None:
        """
        Immediately unload a model and reclaim GPU memory.
        Calls torch.cuda.empty_cache() + gc.collect() for real VRAM release.
        """
        if name not in self._models:
            return
        logger.info(f"[ModelManager] Unloading '{name}'…")
        del self._models[name]
        _torch_empty_cache()
        gc.collect()
        logger.info(f"[ModelManager] '{name}' unloaded — GPU cache cleared.")

    async def unload_idle(self) -> None:
        """
        Unload models that have not been used for TTL seconds.
        Call this from a periodic background task (every 2 minutes).
        """
        now = time.time()
        idle = [
            name for name, (_, last_used) in self._models.items()
            if now - last_used > self._ttl
        ]
        for name in idle:
            self.unload(name)

    # ── Status helper ─────────────────────────────────────────────────────────

    def status(self) -> dict:
        """Returns current loaded models + free VRAM for /health endpoint."""
        return {
            "loaded_models": list(self._models.keys()),
            "free_vram_mb":  _get_free_vram_mb(),
            "vram_floor_mb": _VRAM_FLOOR_MB,
        }


# ── Global singleton ──────────────────────────────────────────────────────────
model_manager = LazyModelManager(ttl_seconds=300)
