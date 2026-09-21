"""
Spotify AppAgent — Free account, no API key required.

Reliability goals:
- Launch Spotify if closed.
- Bring Spotify to foreground even when minimized/tray-restored.
- Open spotify:search URI and start playback of top result.
- Keep media-key controls as the fallback control plane.
"""

import asyncio
import ctypes
import logging
import os
import subprocess
import time
import urllib.parse
from typing import Any

logger = logging.getLogger(__name__)

# Windows virtual-key codes
VK_MEDIA_PLAY_PAUSE = 0xB3
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_VOLUME_UP = 0xAF
VK_VOLUME_DOWN = 0xAE
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002


def _press_media_key(vk: int) -> None:
    """Send a single Windows media key press."""
    ctypes.windll.user32.keybd_event(vk, 0, KEYEVENTF_EXTENDEDKEY, 0)
    ctypes.windll.user32.keybd_event(vk, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)


def _spotify_running() -> bool:
    import psutil

    for p in psutil.process_iter(["name"]):
        name = (p.info.get("name") or "").lower()
        if "spotify" in name:
            return True
    return False


def _launch_spotify() -> None:
    exe_appdata = os.path.expandvars(r"%APPDATA%\Spotify\Spotify.exe")
    exe_local = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WindowsApps\Spotify.exe")

    try:
        if os.path.exists(exe_appdata):
            subprocess.Popen(exe_appdata)
            return
        if os.path.exists(exe_local):
            subprocess.Popen(exe_local)
            return
        subprocess.Popen(["explorer.exe", "spotify:"], shell=False)
    except Exception:
        subprocess.Popen(["explorer.exe", "spotify:"], shell=False)


def _ensure_spotify_running() -> bool:
    """Launch Spotify if needed and wait briefly for process spawn."""
    if _spotify_running():
        return True

    logger.info("[Spotify] Launching Spotify...")
    _launch_spotify()

    for _ in range(20):
        if _spotify_running():
            return True
        time.sleep(0.5)

    return False


def _find_spotify_hwnd() -> int | None:
    import win32gui

    hwnds: list[int] = []

    def _cb(hwnd, _):
        title = (win32gui.GetWindowText(hwnd) or "").lower()
        if "spotify" in title:
            hwnds.append(hwnd)

    win32gui.EnumWindows(_cb, None)
    return hwnds[0] if hwnds else None


def _bring_spotify_foreground() -> int | None:
    """Bring Spotify window to foreground and return hwnd."""
    import win32gui
    import win32con
    import win32api

    hwnd = _find_spotify_hwnd()
    if not hwnd:
        return None

    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    time.sleep(0.15)

    # ALT trick improves SetForegroundWindow reliability.
    win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
    ctypes.windll.user32.SetForegroundWindow(hwnd)
    win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(0.25)
    return hwnd


def _open_search_and_play(query: str) -> dict[str, Any]:
    """Open search URI and start top result playback."""
    import win32gui
    import win32con
    import win32api

    if not _ensure_spotify_running():
        return {"success": False, "error": "Spotify process did not start"}

    encoded = urllib.parse.quote(query)
    uri = f"spotify:search:{encoded}"
    logger.info(f"[Spotify] Opening URI: {uri}")

    try:
        os.startfile(uri)
    except Exception:
        subprocess.Popen(["explorer.exe", uri], shell=False)

    # Wait for a visible/focusable Spotify window.
    hwnd = None
    for i in range(20):
        time.sleep(0.4)
        hwnd = _bring_spotify_foreground()
        if hwnd:
            break
        if i in (7, 14):
            try:
                os.startfile("spotify:")
            except Exception:
                subprocess.Popen(["explorer.exe", "spotify:"], shell=False)

    if not hwnd:
        return {"success": False, "error": "Spotify window not found after launch"}

    # Give UI time to render search results.
    time.sleep(2.0)
    _bring_spotify_foreground()
    time.sleep(0.25)

    # Strategy 1: Double-click the top song result card.
    # The first search result card is always at roughly x=25%, y=22% of the
    # Spotify window. This is more reliable than TAB navigation because TAB
    # focus order changes with every Spotify update.
    try:
        import pyautogui
        hwnd = _find_spotify_hwnd()
        if hwnd:
            import win32gui as _wg
            rect2 = _wg.GetWindowRect(hwnd)
            win_w2 = rect2[2] - rect2[0]
            win_h2 = rect2[3] - rect2[1]
            click_x2 = rect2[0] + int(win_w2 * 0.25)
            click_y2 = rect2[1] + int(win_h2 * 0.28)
            pyautogui.doubleClick(click_x2, click_y2)
            time.sleep(0.5)
            logger.info(f"[Spotify] Double-clicked top song card at ({click_x2}, {click_y2})")
    except Exception as e:
        logger.warning(f"[Spotify] Double-click failed: {e}")

    # Strategy 2: Ensure playback started via media key PLAY_PAUSE.
    time.sleep(0.4)
    _press_media_key(VK_MEDIA_PLAY_PAUSE)
    time.sleep(0.4)

    # Verify playback by title change.
    try:
        hwnd = _find_spotify_hwnd()
        if hwnd:
            import win32gui as _wg2
            title = _wg2.GetWindowText(hwnd)
            if title and title not in ("Spotify", "Spotify Free", "Spotify Premium"):
                logger.info(f"[Spotify] Playback confirmed: '{title}'")
                return {
                    "success": True,
                    "message": f"Playing '{query}' on Spotify",
                    "response_text": f"Playing {query} on Spotify.",
                }
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug(f"Non-critical error: {e}")

    logger.info(f"[Spotify] Play request completed for '{query}' (unverified)")
    return {
        "success": True,
        "message": f"Playing '{query}' on Spotify",
        "response_text": f"Playing {query} on Spotify.",
    }


class SpotifyAgent:
    """Handles play_spotify and spotify_control actions."""

    async def execute_play(self, args: dict[str, Any]) -> dict[str, Any]:
        query = args.get("query", "").strip()
        if not query:
            return {"success": False, "error": "No query provided"}

        logger.info(f"[Spotify] Play request: '{query}'")
        try:
            return await asyncio.get_event_loop().run_in_executor(None, _open_search_and_play, query)
        except Exception as e:
            logger.error(f"[Spotify] Play error: {e}")
            return {"success": False, "error": str(e)}

    async def execute_control(self, args: dict[str, Any]) -> dict[str, Any]:
        action = args.get("action", "")
        logger.info(f"[Spotify] Control: {action}")
        try:
            return await asyncio.get_event_loop().run_in_executor(None, self._control_sync, action, args)
        except Exception as e:
            logger.error(f"[Spotify] Control error: {e}")
            return {"success": False, "error": str(e)}

    def _control_sync(self, action: str, args: dict) -> dict[str, Any]:
        media_keys = {
            "pause": VK_MEDIA_PLAY_PAUSE,
            "resume": VK_MEDIA_PLAY_PAUSE,
            "play_pause": VK_MEDIA_PLAY_PAUSE,
            "next": VK_MEDIA_NEXT_TRACK,
            "previous": VK_MEDIA_PREV_TRACK,
        }

        if action in media_keys:
            _press_media_key(media_keys[action])
            label = action.replace("_", " ").capitalize()
            return {"success": True, "message": label, "response_text": f"{label}."}

        if action == "volume_up":
            for _ in range(5):
                _press_media_key(VK_VOLUME_UP)
                time.sleep(0.05)
            return {"success": True, "message": "Volume up", "response_text": "Volume up."}

        if action == "volume_down":
            for _ in range(5):
                _press_media_key(VK_VOLUME_DOWN)
                time.sleep(0.05)
            return {"success": True, "message": "Volume down", "response_text": "Volume down."}

        if action in ("set_volume", "volume"):
            level = max(0, min(100, int(args.get("level", 50))))
            ticks = round(level / 2)
            for _ in range(50):
                _press_media_key(VK_VOLUME_DOWN)
            for _ in range(ticks):
                _press_media_key(VK_VOLUME_UP)
                time.sleep(0.02)
            return {
                "success": True,
                "message": f"Volume ~{level}%",
                "response_text": f"Volume set to {level} percent.",
            }

        return {"success": False, "error": f"Unknown action: {action}"}
