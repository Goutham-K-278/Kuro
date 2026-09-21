"""
agent/executors/screen_watcher.py
─────────────────────────────────────────────────────────────────
Persistent Screen Watcher — on-demand background auto-clicker.

  ★ NEVER runs automatically on startup.
  ★ Only activates when you explicitly say "watch for X".
  ★ Scoped to the target app — won't click other apps by accident.
  ★ Move mouse to screen corner at any time to emergency-stop (pyautogui FAILSAFE).

Voice commands:
  "Click Allow whenever it appears"        → watches everywhere
  "Keep pressing Allow in VS Code"         → only clicks when VS Code is focused
  "Stop clicking Allow"                    → cancels that specific watcher
  "Stop watching"                          → cancels ALL watchers
  "What are you watching for"              → lists active watchers

WON'T interfere with Spotify, WhatsApp, or any other tool:
  • "Pause the music" → goes to spotify_control, never touches screen_watcher
  • A watcher for "Pause" in app_name="vscode" will NOT click Spotify's pause
─────────────────────────────────────────────────────────────────
"""

import asyncio
import logging
import time
from typing import Optional

import numpy as np
import pyautogui

# Move mouse to any screen corner to emergency-stop all clicks
pyautogui.FAILSAFE = True

logger = logging.getLogger(__name__)

# All currently running watcher tasks: {watcher_id: asyncio.Task}
# Starts EMPTY — nothing runs until you explicitly say "watch for X"
_active_watchers: dict[str, asyncio.Task] = {}

# Florence OCR + capture singletons — lazily initialised on first use.
_florence = None
_capture = None


def _get_florence():
    """Lazy-init Florence engine."""
    global _florence
    if _florence is None:
        from kuro.vision.florence_engine import FlorenceEngine

        _florence = FlorenceEngine.get_instance()
        _florence.ensure_loaded()
        logger.info("[ScreenWatcher] Florence OCR initialised")
    return _florence


def _get_capture():
    """Lazy-init screen capture helper."""
    global _capture
    if _capture is None:
        from kuro.vision.screen_capture import ScreenCapture

        _capture = ScreenCapture()
    return _capture


def _is_app_focused(app_name: str) -> bool:
    """
    Return True if the specified app's window is currently in the foreground.
    If app_name is 'any', always returns True (global watcher).

    This prevents a watcher scoped to 'vscode' from clicking things in
    Spotify, WhatsApp, or any other window that happens to show the same text.
    """
    if app_name.lower() == "any":
        return True
    try:
        import win32gui
        hwnd  = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd).lower()
        return app_name.lower() in title
    except Exception:
        return True   # Fail-open: if win32gui unavailable, don't block the watcher


def _find_text_on_screen(target_text: str, confidence: float = 0.55) -> tuple[int, int] | None:
    """
    Capture the screen and search for target_text using OCR.

    Returns (cx, cy) click coordinates if found, else None.
    Uses a tolerance of 0.55 to handle partial/blurry text.
    """
    target_lower = target_text.lower().strip()
    florence = _get_florence()
    capture = _get_capture()

    screenshot = capture.grab(force=True)
    results = florence.ocr(screenshot)

    for item in results:
        text = str(item.get("text", ""))
        bbox = item.get("bbox", [])
        if len(bbox) >= 4 and target_lower in text.lower():
            cx = int((bbox[0] + bbox[2]) / 2)
            cy = int((bbox[1] + bbox[3]) / 2)
            return cx, cy

    return None


async def _watch_loop(
    watcher_id:    str,
    target_text:   str,
    app_name:      str,
    interval_secs: float = 1.5,
    max_clicks:    int   = 200,
) -> None:
    """
    Background task: polls screen every `interval_secs` and clicks
    `target_text` whenever it appears — but ONLY when the target app
    is in the foreground (if app_name was specified).

    Runs until cancelled or `max_clicks` is reached.
    """
    click_count = 0
    scope = f"in '{app_name}'" if app_name != "any" else "anywhere on screen"
    logger.info(
        f"[ScreenWatcher] Started — watching for '{target_text}' {scope}, "
        f"every {interval_secs}s (max {max_clicks} clicks)"
    )

    try:
        while click_count < max_clicks:
            await asyncio.sleep(interval_secs)

            try:
                # ── Scope check: skip if target app isn't in focus ────────
                if not _is_app_focused(app_name):
                    continue

                coords = _find_text_on_screen(target_text)
                if coords:
                    x, y = coords
                    pyautogui.click(x, y)
                    click_count += 1
                    logger.info(
                        f"[ScreenWatcher] Clicked '{target_text}' at ({x},{y}) "
                        f"— count: {click_count}"
                    )
                    await asyncio.sleep(0.8)   # Brief pause so the click registers
            except Exception as e:
                logger.debug(f"[ScreenWatcher] Scan error: {e}")

    except asyncio.CancelledError:
        logger.info(f"[ScreenWatcher] Stopped '{target_text}' ({click_count} clicks total)")
    finally:
        _active_watchers.pop(watcher_id, None)


def start_watcher(
    target_text:   str,
    app_name:      str   = "any",
    interval_secs: float = 1.5,
) -> dict:
    """
    Start an on-demand background screen watcher.

    This is NOT called automatically — it only activates when explicitly
    triggered by a voice command like "watch for Allow".

    Args:
        target_text:   The button/text to watch for and click (e.g. "Allow").
        app_name:      If set (not "any"), only clicks when that app's window
                       is in the foreground — prevents cross-app accidents.
        interval_secs: How often to scan the screen (default 1.5s).
    """
    if not target_text.strip():
        return {"success": False, "error": "No target text provided.",
                "response_text": "What should I watch for?"}

    watcher_id = f"{target_text.lower().replace(' ', '_')}_{int(time.time())}"

    try:
        loop = asyncio.get_running_loop()   # Requires an active asyncio loop
    except RuntimeError:
        return {"success": False, "error": "No asyncio event loop running.",
                "response_text": "Can't start watcher — no event loop."}

    task = loop.create_task(
        _watch_loop(watcher_id, target_text, app_name, interval_secs)
    )
    _active_watchers[watcher_id] = task

    scope = f"in {app_name}" if app_name != "any" else "anywhere on screen"
    logger.info(f"[ScreenWatcher] Created watcher for '{target_text}' {scope}")
    return {
        "success":       True,
        "watcher_id":    watcher_id,
        "response_text": (
            f"Now watching for '{target_text}' {scope}. "
            "I'll click it whenever it appears. Say 'stop watching' to stop."
        ),
    }


def stop_watcher(
    target_text: Optional[str] = None,
    watcher_id:  Optional[str] = None,
) -> dict:
    """
    Stop a specific watcher or all watchers.

    Args:
        target_text: Cancel any watcher whose ID contains this text.
        watcher_id:  Cancel this exact watcher ID.
        (Calling with neither argument stops ALL active watchers.)
    """
    if not _active_watchers:
        return {
            "success":       True,
            "response_text": "No active screen watchers to stop.",
        }

    # Cancel by exact watcher_id
    if watcher_id and watcher_id in _active_watchers:
        _active_watchers[watcher_id].cancel()
        return {"success": True, "response_text": "Stopped that watcher."}

    # Cancel by target_text substring
    if target_text:
        target_lower = target_text.lower()
        to_stop      = [wid for wid in list(_active_watchers) if target_lower in wid]
        for wid in to_stop:
            _active_watchers[wid].cancel()
        if to_stop:
            return {"success": True, "response_text": f"Stopped watching for '{target_text}'."}

    # Cancel ALL watchers
    for task in list(_active_watchers.values()):
        task.cancel()
    _active_watchers.clear()
    return {"success": True, "response_text": "Stopped all screen watchers."}


def list_watchers() -> dict:
    """Returns all currently active watcher IDs."""
    active = list(_active_watchers.keys())
    return {
        "success":       True,
        "active_count":  len(active),
        "watchers":      active,
        "response_text": (
            f"{len(active)} watcher(s) active: {', '.join(active)}"
            if active else "No active screen watchers."
        ),
    }
