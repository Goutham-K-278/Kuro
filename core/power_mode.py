"""
KURO — Dynamic Power Mode (Phase 8 rewrite)
=============================================
Original: power mode read ONCE at startup, never updated.
Now:      PowerModeWatcher background task polls every 60 s and
          hot-swaps Whisper thread count + GPU layer count live.

Usage
─────
# In main.py lifespan, AFTER the pipeline is created:
    from core.power_mode import PowerModeWatcher, get_current_config
    asyncio.create_task(PowerModeWatcher().run())

# Anywhere in the codebase that needs current settings:
    from core.power_mode import get_current_config
    cfg = get_current_config()
    threads = cfg["whisper_threads"]
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
import time

import psutil

logger = logging.getLogger(__name__)


# ── Mode detection ────────────────────────────────────────────────────────────

def get_laptop_power_mode() -> str:
    """
    Detects the active Windows power scheme.
    Returns: "performance" | "balanced" | "quiet"
    """
    try:
        result = subprocess.run(
            ["powercfg", "/getactivescheme"],
            capture_output=True, timeout=2, text=True,
        ).stdout.strip().lower()

        if any(kw in result for kw in ("power saver", "silent", "quiet", "eco")):
            return "quiet"
        if any(kw in result for kw in ("high performance", "ultimate", "turbo")):
            return "performance"
        if "balanced" in result:
            # On-battery balanced → treat as quiet
            try:
                battery = psutil.sensors_battery()
                if battery and not battery.power_plugged:
                    return "quiet"
            except Exception as e:
                import logging
                logging.getLogger(__name__).debug(f"Non-critical error: {e}")
            return "balanced"
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug(f"Non-critical error: {e}")
    return "balanced"


def get_cpu_temp() -> float:
    """Returns CPU temp in °C. Returns 0.0 if unavailable."""
    try:
        import wmi
        w = wmi.WMI(namespace="root\\OpenHardwareMonitor")
        temps = [s.Value for s in w.Sensor() if s.SensorType == "Temperature" and "CPU" in s.Name]
        return max(temps) if temps else 0.0
    except Exception:
        return 0.0


# ── Config profiles ───────────────────────────────────────────────────────────

_PROFILES: dict[str, dict] = {
    "performance": {
        "whisper_threads":          4,
        "llm_gpu_layers":           35,    # All on GPU
        "deepfilternet_device":     "cpu", # E-cores
        "easyocr_gpu":              False,
        "tts_cache_only":           False,
        "max_concurrent_tasks":     4,
        "vram_floor_mb":            600,   # Minimum free VRAM to allow GPU loads
    },
    "balanced": {
        "whisper_threads":          3,
        "llm_gpu_layers":           28,
        "deepfilternet_device":     "cpu",
        "easyocr_gpu":              False,
        "tts_cache_only":           False,
        "max_concurrent_tasks":     3,
        "vram_floor_mb":            700,
    },
    "quiet": {
        "whisper_threads":          2,    # Fewer threads = less heat
        "llm_gpu_layers":           0,    # Full CPU offload in quiet mode
        "deepfilternet_device":     "cpu",
        "easyocr_gpu":              False,
        "tts_cache_only":           True, # Only cached audio, skip new synthesis
        "max_concurrent_tasks":     2,
        "vram_floor_mb":            900,  # Higher floor — be very conservative
    },
}

# ── Live state (updated by PowerModeWatcher) ──────────────────────────────────

_current_mode:   str  = "balanced"
_current_config: dict = dict(_PROFILES["balanced"])
_last_mode:      str  = ""  # tracks previous mode for change detection


def get_current_config() -> dict:
    """
    Returns the live power-aware config dict.
    Always up-to-date — PowerModeWatcher refreshes it every 60 s.
    """
    return dict(_current_config)


def get_current_mode() -> str:
    return _current_mode


# ── PowerAwareConfig shim (backward compat with old call sites) ──────────────

class PowerAwareConfig:
    """Legacy shim — old code called PowerAwareConfig.get() at startup."""
    @staticmethod
    def get() -> dict:
        return get_current_config()


# ── Thermal throttle ─────────────────────────────────────────────────────────

_THERMAL_THROTTLE_C = 88.0   # °C — if CPU hits this, drop to quiet


def _thermal_mode_override(base_mode: str) -> str:
    """
    If CPU temp exceeds threshold, force quiet mode regardless of power scheme.
    Only applies when OpenHardwareMonitor is available.
    """
    temp = get_cpu_temp()
    if temp > 0 and temp >= _THERMAL_THROTTLE_C:
        logger.warning(f"[PowerMode] CPU temp {temp:.0f}°C ≥ {_THERMAL_THROTTLE_C}°C — forcing quiet mode")
        return "quiet"
    return base_mode


# ── Background watcher ────────────────────────────────────────────────────────

class PowerModeWatcher:
    """
    Async background task that polls the Windows power scheme every `interval_s`
    seconds and hot-swaps the live config profile.

    Add to main.py lifespan:
        asyncio.create_task(PowerModeWatcher().run())
    """

    def __init__(self, interval_s: float = 60.0):
        self._interval = interval_s
        self._running  = True

    def stop(self) -> None:
        self._running = False

    async def run(self) -> None:
        global _current_mode, _current_config, _last_mode

        logger.info("[PowerMode] Watcher started — polling every %ss", self._interval)

        while self._running:
            try:
                loop = asyncio.get_event_loop()
                # powercfg is a fast subprocess — run in executor to not block
                raw_mode = await loop.run_in_executor(None, get_laptop_power_mode)
                mode     = await loop.run_in_executor(None, _thermal_mode_override, raw_mode)

                if mode != _last_mode:
                    _last_mode      = mode
                    _current_mode   = mode
                    _current_config = dict(_PROFILES[mode])
                    logger.info(
                        "[PowerMode] ⚡ Mode changed: %s → %s | "
                        "whisper_threads=%d, gpu_layers=%d, tts_cache_only=%s",
                        _last_mode, mode,
                        _current_config["whisper_threads"],
                        _current_config["llm_gpu_layers"],
                        _current_config["tts_cache_only"],
                    )
                    # Notify model_manager of new VRAM floor
                    try:
                        from core.model_manager import model_manager
                        model_manager._VRAM_FLOOR_MB = _current_config["vram_floor_mb"]
                    except Exception as e:
                        import logging
                        logging.getLogger(__name__).debug(f"Non-critical error: {e}")

            except Exception as e:
                logger.debug(f"[PowerMode] Watcher tick error: {e}")

            await asyncio.sleep(self._interval)

        logger.info("[PowerMode] Watcher stopped.")
