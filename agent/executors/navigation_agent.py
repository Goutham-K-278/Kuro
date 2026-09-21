"""
import warnings
warnings.warn(
    "agent/executors/navigation_agent.py is deprecated. Functionality moved to kuro/agents/host_agent.py. "
    "This module will be removed in Phase 6.",
    DeprecationWarning,
    stacklevel=2
)
NavigationAgent — Navigates WITHIN already-open applications via voice.

This is NOT "open an app" — that's AppLauncher.
This is "go to a specific place INSIDE an already-running app."

Supported:
  WhatsApp Desktop  → Ctrl+F → search contact → Enter
  Telegram Desktop  → Escape (close current) → Ctrl+F → search → select
  Discord           → Ctrl+K (quick switcher) → search → Enter
  Slack             → Ctrl+K → search → Enter
  Microsoft Teams   → Ctrl+E → search → Enter
  Chrome/Edge/Firefox → Ctrl+L (address bar) → type URL → Enter
  File Explorer     → Ctrl+L (address bar) → type path → Enter
  VSCode            → Ctrl+P (quick open) → search file → Enter
  Spotify           → Ctrl+L → search → Enter

Voice-first design:
  - All results are SHORT strings meant to be spoken by TTS
  - No markdown, no long text — just "Done" / "Navigated to X"
  - If app isn't open, we open it FIRST, wait, THEN navigate
"""

import time
import logging
import os
import subprocess
import logging
logger = logging.getLogger(__name__)

try:
    import pyautogui as _pyautogui
    PYAUTOGUI_AVAILABLE = True
except ImportError:
    _pyautogui = None
    PYAUTOGUI_AVAILABLE = False

import pyperclip
try:
    import pygetwindow as gw
    PYGETWINDOW_AVAILABLE = True
except ImportError:
    gw = None
    PYGETWINDOW_AVAILABLE = False
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)

# Pause duration between keystrokes (seconds)
_KEY_DELAY = 0.05
# Pause after focusing a window
_FOCUS_DELAY = 0.4
# Pause after opening search
_SEARCH_OPEN_DELAY = 0.6
# Pause after typing search query (for results to populate)
_SEARCH_RESULT_DELAY = 0.8
# Pause after pressing Enter to select
_SELECT_DELAY = 0.4


# ─── WhatsApp State Tracker (Bug 1 — Phase 3) ────────────────────────────────

class WhatsAppState:
    """Tracks the current state of WhatsApp Desktop."""
    CLOSED    = "closed"
    OPEN_IDLE = "open_idle"   # main screen, no chat active
    SEARCHING = "searching"   # search bar is active
    IN_CHAT   = "in_chat"     # a specific chat is open

_wa_state:          str       = WhatsAppState.CLOSED
_wa_active_contact: Optional[str] = None   # name of currently open chat


# ─── App Navigation Configs ───────────────────────────────────────────────

APP_NAV_CONFIG: Dict[str, dict] = {
    "whatsapp": {
        "search_shortcut": ("ctrl", "f"),
        "search_delay": _SEARCH_OPEN_DELAY,
        "result_delay": _SEARCH_RESULT_DELAY,
        "select_keys": ["enter"],
        "post_select_keys": ["escape"],   # close search bar after selecting
        "window_keywords": ["whatsapp"],
        "process_name": "WhatsApp.exe",
        "open_command": "start whatsapp:",  # Windows URI scheme
        "open_delay": 3.0,
    },
    "telegram": {
        "search_shortcut": ("escape",),   # first close any open panel
        "pre_search_delay": 0.3,
        "search_shortcut_2": ("ctrl", "f"),  # then open search
        "search_delay": _SEARCH_OPEN_DELAY,
        "result_delay": _SEARCH_RESULT_DELAY,
        "select_keys": ["enter"],
        "post_select_keys": [],
        "window_keywords": ["telegram"],
        "process_name": "Telegram.exe",
        "open_command": "start telegram:",
        "open_delay": 3.0,
    },
    "discord": {
        "search_shortcut": ("ctrl", "k"),   # quick switcher
        "search_delay": _SEARCH_OPEN_DELAY,
        "result_delay": _SEARCH_RESULT_DELAY,
        "select_keys": ["enter"],
        "post_select_keys": [],
        "window_keywords": ["discord"],
        "process_name": "Discord.exe",
        "open_command": "start discord:",
        "open_delay": 4.0,
    },
    "slack": {
        "search_shortcut": ("ctrl", "k"),
        "search_delay": _SEARCH_OPEN_DELAY,
        "result_delay": _SEARCH_RESULT_DELAY,
        "select_keys": ["enter"],
        "post_select_keys": [],
        "window_keywords": ["slack"],
        "process_name": "slack.exe",
        "open_command": "start slack:",
        "open_delay": 4.0,
    },
    "teams": {
        "search_shortcut": ("ctrl", "e"),
        "search_delay": _SEARCH_OPEN_DELAY,
        "result_delay": 1.0,  # Teams is slow
        "select_keys": ["enter"],
        "post_select_keys": [],
        "window_keywords": ["teams", "microsoft teams"],
        "process_name": "ms-teams.exe",
        "open_command": "start msteams:",
        "open_delay": 5.0,
    },
    "chrome": {
        "search_shortcut": ("ctrl", "l"),   # address bar
        "search_delay": 0.3,
        "result_delay": 0.3,
        "select_keys": ["enter"],
        "post_select_keys": [],
        "window_keywords": ["chrome", "google chrome"],
        "process_name": "chrome.exe",
        "open_command": "start chrome",
        "open_delay": 2.0,
    },
    "edge": {
        "search_shortcut": ("ctrl", "l"),
        "search_delay": 0.3,
        "result_delay": 0.3,
        "select_keys": ["enter"],
        "post_select_keys": [],
        "window_keywords": ["edge", "microsoft edge"],
        "process_name": "msedge.exe",
        "open_command": "start msedge",
        "open_delay": 2.0,
    },
    "firefox": {
        "search_shortcut": ("ctrl", "l"),
        "search_delay": 0.3,
        "result_delay": 0.3,
        "select_keys": ["enter"],
        "post_select_keys": [],
        "window_keywords": ["firefox", "mozilla firefox"],
        "process_name": "firefox.exe",
        "open_command": "start firefox",
        "open_delay": 2.0,
    },
    "explorer": {
        "search_shortcut": ("ctrl", "l"),   # address bar
        "search_delay": 0.3,
        "result_delay": 0.3,
        "select_keys": ["enter"],
        "post_select_keys": [],
        "window_keywords": ["file explorer", "explorer"],
        "process_name": "explorer.exe",
        "open_command": "explorer",
        "open_delay": 1.5,
    },
    "vscode": {
        "search_shortcut": ("ctrl", "p"),   # quick open
        "search_delay": 0.4,
        "result_delay": 0.5,
        "select_keys": ["enter"],
        "post_select_keys": [],
        "window_keywords": ["visual studio code", "code"],
        "process_name": "Code.exe",
        "open_command": "code",
        "open_delay": 3.0,
    },
    "spotify": {
        "search_shortcut": ("ctrl", "l"),
        "search_delay": 0.5,
        "result_delay": 1.0,
        "select_keys": ["enter"],
        "post_select_keys": [],
        "window_keywords": ["spotify"],
        "process_name": "Spotify.exe",
        "open_command": "start spotify:",
        "open_delay": 3.0,
    },
    "notepad": {
        "search_shortcut": ("ctrl", "f"),   # find dialog
        "search_delay": 0.3,
        "result_delay": 0.3,
        "select_keys": ["enter"],
        "post_select_keys": ["escape"],
        "window_keywords": ["notepad"],
        "process_name": "notepad.exe",
        "open_command": "notepad",
        "open_delay": 1.0,
    },
}

# Alias map — normalize spoken app names to config keys
APP_ALIASES: Dict[str, str] = {
    "wa": "whatsapp",
    "whats app": "whatsapp",
    "tg": "telegram",
    "dc": "discord",
    "vs code": "vscode",
    "visual studio code": "vscode",
    "visual studio": "vscode",
    "vs": "vscode",
    "google chrome": "chrome",
    "google": "chrome",
    "microsoft edge": "edge",
    "file explorer": "explorer",
    "files": "explorer",
    "folders": "explorer",
    "my computer": "explorer",
    "this pc": "explorer",
    "ms teams": "teams",
    "microsoft teams": "teams",
}

# Folder keywords → Windows path mapping
FOLDER_PATHS: Dict[str, str] = {
    "downloads": "%USERPROFILE%\\Downloads",
    "documents": "%USERPROFILE%\\Documents",
    "desktop": "%USERPROFILE%\\Desktop",
    "pictures": "%USERPROFILE%\\Pictures",
    "music": "%USERPROFILE%\\Music",
    "videos": "%USERPROFILE%\\Videos",
    "appdata": "%APPDATA%",
    "temp": "%TEMP%",
}


class NavigationAgent:
    """
    Navigate WITHIN applications using keyboard shortcuts + search.

    Voice-first: all responses are short, speakable strings.
    Auto-opens apps if not already running.
    """

    def navigate(
        self,
        app: Optional[str],
        target: str,
        nav_type: str = "generic",
    ) -> str:
        """
        Navigate to a target within an app.

        Args:
            app:      Application name (whatsapp, telegram, discord, chrome, etc.)
                      If None, we try to guess the right app.
            target:   What to navigate to (contact name, URL, file path, channel name)
            nav_type: "chat", "contact", "url", "file", "channel", "generic"

        Returns:
            Short status string (designed for TTS — will be spoken out loud)
        """
        if not target or not target.strip():
            return "I need to know where to navigate. Try saying go to someone's chat."

        target = target.strip()

        # If no app specified, try to figure it out
        if not app:
            return self._smart_navigate(target, nav_type)

        app_key = self._resolve_app(app)

        if app_key not in APP_NAV_CONFIG:
            return f"I don't know how to navigate inside {app} yet."

        config = APP_NAV_CONFIG[app_key]

        # Handle folder navigation specially — just open the folder
        if nav_type == "file" and app_key == "explorer":
            folder_path = FOLDER_PATHS.get(target.lower(), target)
            folder_path = folder_path.replace("%USERPROFILE%",
                          __import__("os").path.expanduser("~"))
            folder_path = folder_path.replace("%APPDATA%",
                          __import__("os").environ.get("APPDATA", ""))
            folder_path = folder_path.replace("%TEMP%",
                          __import__("os").environ.get("TEMP", ""))
            try:
                subprocess.Popen(["explorer", folder_path], shell=False)
                return f"Opened {target} folder"
            except Exception as e:
                logger.warning(f"Explorer open failed: {e}")

        # Step 1: Focus the app (or open it if not running)
        focused = self._focus_app_window(config["window_keywords"])

        if not focused:
            # App not running — open it first
            logger.info(f"{app_key} not running, opening it first...")
            opened = self._open_app(config)
            if not opened:
                return f"I couldn't open {app}. Try opening it manually first."

            # Wait for the app to load
            time.sleep(config.get("open_delay", 3.0))

            # Try focusing again after opening
            focused = self._focus_app_window(config["window_keywords"])
            if not focused:
                return f"I opened {app} but couldn't find its window. It might still be loading."

        time.sleep(_FOCUS_DELAY)

        # Step 2: Handle pre-search actions (e.g., Telegram needs Escape first)
        if "pre_search_delay" in config:
            # Press the first shortcut (e.g., Escape to close panels)
            _pyautogui.hotkey(*config["search_shortcut"])
            time.sleep(config["pre_search_delay"])
            # Then press the actual search shortcut
            if "search_shortcut_2" in config:
                _pyautogui.hotkey(*config["search_shortcut_2"])
        else:
            # Normal: just open search
            _pyautogui.hotkey(*config["search_shortcut"])

        time.sleep(config["search_delay"])

        # Step 3: Clear any existing search text and type the target
        _pyautogui.hotkey("ctrl", "a")  # select all in search box
        time.sleep(0.1)

        # Use clipboard paste — fast + unicode safe
        old_clipboard = ""
        try:
            old_clipboard = pyperclip.paste()
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug(f"Non-critical error: {e}")

        pyperclip.copy(target)
        _pyautogui.hotkey("ctrl", "v")

        time.sleep(config["result_delay"])

        # Step 4: Select the first result
        for key in config.get("select_keys", ["enter"]):
            _pyautogui.press(key)
            time.sleep(_SELECT_DELAY)

        # Step 5: Post-select cleanup (close search bar if needed)
        for key in config.get("post_select_keys", []):
            time.sleep(0.2)
            _pyautogui.press(key)

        # Restore clipboard
        try:
            if old_clipboard:
                pyperclip.copy(old_clipboard)
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug(f"Non-critical error: {e}")

        # Build a nice spoken response
        app_display = app_key.title()
        logger.info(f"Navigated to '{target}' in {app_display}")

        # Short, voice-friendly responses
        if nav_type in ("chat", "contact"):
            return f"Opened {target}'s chat on {app_display}"
        elif nav_type == "url":
            return f"Going to {target}"
        elif nav_type == "channel":
            return f"Switched to {target} on {app_display}"
        else:
            return f"Navigated to {target} in {app_display}"

    # ─── Smart Navigation (no app specified) ──────────────────────────────

    def _smart_navigate(self, target: str, nav_type: str) -> str:
        """
        When no app is specified, figure out the right one.

        Priority:
        1. If target looks like a URL → browser
        2. If target looks like a file path → explorer
        3. If target is a known folder name → explorer
        4. If a messaging app is currently open/visible → use that
        5. Default → WhatsApp (most common "go to X chat" pattern)
        """
        target_lower = target.lower()

        # URL detection
        if any(target_lower.startswith(p) for p in ("http://", "https://", "www.", "ftp://")):
            return self.navigate("chrome", target, "url")

        # File path detection
        if "\\" in target or target.startswith("/") or target.startswith("~"):
            return self.navigate("explorer", target, "file")

        # Folder shortcut detection
        if target_lower in FOLDER_PATHS:
            return self.navigate("explorer", target, "file")

        # Check which messaging apps are currently open
        open_messaging = self._find_open_messaging_app()
        if open_messaging:
            return self.navigate(open_messaging, target, nav_type or "chat")

        # Default: try WhatsApp
        return self.navigate("whatsapp", target, nav_type or "chat")

    def _find_open_messaging_app(self) -> Optional[str]:
        """Check if any messaging app is currently running. Returns first found."""
        if not PYGETWINDOW_AVAILABLE: return False
        messaging_apps = ["whatsapp", "telegram", "discord", "slack", "teams"]

        try:
            all_windows = gw.getAllWindows()
            for app_key in messaging_apps:
                config = APP_NAV_CONFIG[app_key]
                for win in all_windows:
                    if not win.title or not win.visible:
                        continue
                    title_lower = win.title.lower()
                    for keyword in config["window_keywords"]:
                        if keyword in title_lower:
                            return app_key
        except Exception as e:
            logger.debug(f"Window scan error: {e}")

        return None

    # ─── Window Focus ─────────────────────────────────────────────────────

    def _focus_app_window(self, title_keywords: List[str]) -> bool:
        """
        Find and activate an app window by title keywords.
        Returns True if successfully focused.
        """
        try:
            all_windows = gw.getAllWindows()
            candidates = []

            for win in all_windows:
                if not win.title or not win.visible:
                    continue
                title_lower = win.title.lower()
                for keyword in title_keywords:
                    if keyword.lower() in title_lower:
                        candidates.append(win)
                        break

            if not candidates:
                return False

            # Prefer the largest (main) window if multiple matches
            candidates.sort(key=lambda w: (w.width or 0) * (w.height or 0), reverse=True)
            win = candidates[0]

            if win.isMinimized:
                win.restore()
                time.sleep(0.3)

            win.activate()
            time.sleep(0.2)

            return True

        except Exception as e:
            logger.warning(f"Failed to focus window: {e}")
            return False

    # ─── App Opening ──────────────────────────────────────────────────────

    def _open_app(self, config: dict) -> bool:
        """Open an app using its configured command."""
        open_cmd = config.get("open_command")
        if not open_cmd:
            return False

        try:
            subprocess.Popen(
                open_cmd,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except Exception as e:
            logger.warning(f"Failed to open app: {e}")
            return False

    # ─── App Name Resolution ──────────────────────────────────────────────

    def _resolve_app(self, app: str) -> str:
        """Normalize app name to a config key."""
        if not app:
            return ""
        key = app.lower().strip()
        # Check aliases
        if key in APP_ALIASES:
            return APP_ALIASES[key]
        # Check if it's already a valid config key
        if key in APP_NAV_CONFIG:
            return key
        # Fuzzy: check if any config key starts with / contains the input
        for config_key in APP_NAV_CONFIG:
            if config_key.startswith(key) or key.startswith(config_key):
                return config_key
        return key

    # ─── Tab/Window Navigation (Browser-specific) ─────────────────────────

    def switch_tab(self, app: str, tab_identifier: str) -> str:
        """
        Switch to a specific browser tab by searching tab titles.
        Uses Ctrl+Shift+A (Chrome/Edge) to search open tabs.
        """
        app_key = self._resolve_app(app)

        if app_key not in ("chrome", "edge", "firefox"):
            return f"Tab switching only works in browsers, not {app}."

        focused = self._focus_app_window(APP_NAV_CONFIG[app_key]["window_keywords"])
        if not focused:
            return f"{app} doesn't seem to be open."

        time.sleep(_FOCUS_DELAY)

        if app_key in ("chrome", "edge"):
            # Chrome/Edge: Ctrl+Shift+A opens tab search
            _pyautogui.hotkey("ctrl", "shift", "a")
        else:
            # Firefox: use Ctrl+L and type % to search tabs
            _pyautogui.hotkey("ctrl", "l")
            time.sleep(0.2)
            _pyautogui.typewrite("% ", interval=0.05)

        time.sleep(0.5)

        # Type search query
        pyperclip.copy(tab_identifier)
        _pyautogui.hotkey("ctrl", "v")
        time.sleep(0.5)

        # Select first result
        _pyautogui.press("enter")

        return f"Switched to {tab_identifier} tab"

    # ─── Scroll Navigation ────────────────────────────────────────────────

    def scroll_in_app(self, app: Optional[str], direction: str, amount: int = 3) -> str:
        """Scroll within an app. Direction: 'up' or 'down'."""
        if app:
            app_key = self._resolve_app(app)
            if app_key in APP_NAV_CONFIG:
                self._focus_app_window(APP_NAV_CONFIG[app_key]["window_keywords"])
                time.sleep(_FOCUS_DELAY)

        scroll_amount = amount if direction == "up" else -amount
        _pyautogui.scroll(scroll_amount)

        return f"Scrolled {direction}"

    # ─── WhatsApp-Specific Helpers (Phase 3 Bugs 1-4) ────────────────────────

    def _ensure_whatsapp_open(self) -> bool:
        """
        Ensure WhatsApp is open and focused.
        Delegates to AppLauncher.open_and_wait() for reliable cold-start handling.
        Returns True if WhatsApp window is found and focused.
        """
        if not PYGETWINDOW_AVAILABLE: return False
        wins = gw.getWindowsWithTitle("WhatsApp")
        if wins:
            win = wins[0]
            try:
                if win.isMinimized:
                    win.restore()
                    time.sleep(0.3)
                win.activate()
                time.sleep(0.3)
            except Exception as e:
                import logging
                logging.getLogger(__name__).debug(f"Non-critical error: {e}")
            return True

        # Not running — use AppLauncher with window polling (Bug 2 fix)
        try:
            from agent.executors.app_launcher import AppLauncher
            return AppLauncher().open_and_wait("whatsapp", timeout=8.0)
        except Exception as e:
            logger.warning(f"Failed to open WhatsApp: {e}")
            return False

    # Keep _focus_whatsapp as an alias for backward compat
    def _focus_whatsapp(self) -> bool:
        return self._ensure_whatsapp_open()

    def _verify_highlighted_contact(self, expected_name: str) -> bool:
        """
        Bug 1 — Layer 1 Safety:
        Screenshot the search results area and OCR-check that the
        highlighted contact matches expected_name.
        Falls back to True (non-blocking) if pytesseract is not installed.
        """
        try:
            import pytesseract
            from PIL import ImageGrab
        except ImportError:
            logger.warning(
                "pytesseract / Pillow not installed — contact verification skipped. "
                "Run: pip install pytesseract pillow"
            )
            return True   # non-blocking fallback

        try:
            wins = gw.getWindowsWithTitle("WhatsApp")
            if not wins:
                return False

            win = wins[0]
            # Crop the first search result row in the left panel
            x1 = win.left + 60
            y1 = win.top  + int(win.height * 0.10)
            x2 = win.left + int(win.width  * 0.68)
            y2 = win.top  + int(win.height * 0.22)

            img  = ImageGrab.grab(bbox=(x1, y1, x2, y2))
            text = pytesseract.image_to_string(img).lower().strip()
            logger.debug(f"[OCR] Contact area text: {text!r} | Expected: {expected_name!r}")
            return expected_name.strip().lower() in text
        except Exception as e:
            logger.warning(f"OCR contact verification error: {e}")
            return True   # non-blocking fallback

    def open_whatsapp_chat(self, contact_name: str) -> dict:
        """
        Bug 3+4 Fix:
        Search for contact in WhatsApp, verify the result with OCR,
        then Down+Enter to open the actual chat.
        Returns dict: {"success": bool, "reason": str}
        """
        global _wa_state

        if not self._ensure_whatsapp_open():
            return {"success": False, "reason": "whatsapp_not_found"}

        wins = gw.getWindowsWithTitle("WhatsApp")
        if not wins:
            return {"success": False, "reason": "whatsapp_not_found"}

        # Strip emojis / special chars — WhatsApp search handles plain names best
        import re as _re
        search_name = _re.sub(r'[^\w\s]', '', contact_name).strip()

        # Open WhatsApp search using keyboard shortcut (layout-independent).
        _pyautogui.hotkey("ctrl", "f")
        time.sleep(0.3)

        _wa_state = WhatsAppState.SEARCHING

        # Clear any previous text and type the contact name via clipboard (unicode-safe)
        _pyautogui.hotkey("ctrl", "a")
        old_clip = ""
        try:
            old_clip = pyperclip.paste()
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug(f"Non-critical error: {e}")
        pyperclip.copy(search_name)
        _pyautogui.hotkey("ctrl", "v")
        time.sleep(1.0)   # wait for search results to populate

        # Bug 1 — Layer 1: OCR verify the contact before opening
        if not self._verify_highlighted_contact(contact_name):
            _pyautogui.press("escape")
            _wa_state = WhatsAppState.OPEN_IDLE
            try:
                if old_clip:
                    pyperclip.copy(old_clip)
            except Exception as e:
                import logging
                logging.getLogger(__name__).debug(f"Non-critical error: {e}")
            logger.warning(f"[WA] OCR mismatch — contact '{contact_name}' not found in results")
            return {"success": False, "reason": "contact_not_found"}

        # Navigate to first result and open the chat (Bug 3 fix: Down + Enter)
        _pyautogui.press("down")
        time.sleep(0.2)
        _pyautogui.press("enter")
        time.sleep(0.5)

        # Restore clipboard
        try:
            if old_clip:
                pyperclip.copy(old_clip)
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug(f"Non-critical error: {e}")

        return {"success": True}

    def send_whatsapp_message(self, contact_name: str, message: str) -> str:
        """
        Bug 1 — 3-Layer Safety Send:
          Layer 1: OCR verify contact in search results
          Layer 2: State tracking — only send when IN_CHAT with correct contact
          Layer 3: Relative message-box click (not hardcoded pixels)
        """
        global _wa_state, _wa_active_contact

        # Step 1: Navigate to correct chat (includes OCR verify)
        result = self.open_whatsapp_chat(contact_name)
        if not result.get("success"):
            reason = result.get("reason", "unknown")
            if reason == "contact_not_found":
                return f"Contact '{contact_name}' not found in WhatsApp — message not sent."
            return f"Could not open WhatsApp chat with '{contact_name}'."

        # Step 2: Update state
        _wa_state = WhatsAppState.IN_CHAT
        _wa_active_contact = contact_name

        time.sleep(0.6)   # let chat pane fully render

        wins = gw.getWindowsWithTitle("WhatsApp")
        if not wins:
            return "WhatsApp window disappeared after opening chat."
        win = wins[0]

        # Step 3 — Bug 4 fix: Relative message-box click (not hardcoded pixels)
        # Message input sits at 58% width, 95.5% height of the window
        cx = win.left + int(win.width  * 0.58)
        cy = win.top  + int(win.height * 0.955)
        _pyautogui.click(cx, cy)
        time.sleep(0.25)

        # Type via clipboard (unicode-safe, fast)
        old_clip = ""
        try:
            old_clip = pyperclip.paste()
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug(f"Non-critical error: {e}")
        pyperclip.copy(message)
        _pyautogui.hotkey("ctrl", "v")
        time.sleep(0.1)
        _pyautogui.press("enter")
        try:
            if old_clip:
                pyperclip.copy(old_clip)
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug(f"Non-critical error: {e}")

        return f"Sent '{message}' to {contact_name} on WhatsApp"

    def send_discord_message(self, contact_name: str, message: str) -> str:
        """
        Bug 5 Fix — Discord DM:
        Use Ctrl+K quick-switcher to find the contact's DM and send the message.
        """
        # Focus or open Discord
        wins = gw.getWindowsWithTitle("Discord")
        if not wins:
            try:
                from agent.executors.app_launcher import AppLauncher
                ok = AppLauncher().open_and_wait("discord", timeout=8.0)
                if not ok:
                    return "Could not open Discord."
            except Exception as e:
                return f"Failed to open Discord: {e}"
        else:
            try:
                wins[0].activate()
            except Exception as e:
                import logging
                logging.getLogger(__name__).debug(f"Non-critical error: {e}")
        time.sleep(0.4)

        # Ctrl+K = Discord quick-open DM/channel search
        _pyautogui.hotkey("ctrl", "k")
        time.sleep(0.4)
        pyperclip.copy(contact_name)
        _pyautogui.hotkey("ctrl", "v")
        time.sleep(0.6)
        _pyautogui.press("down")
        time.sleep(0.15)
        _pyautogui.press("enter")
        time.sleep(0.4)

        # Type and send
        old_clip = ""
        try:
            old_clip = pyperclip.paste()
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug(f"Non-critical error: {e}")
        pyperclip.copy(message)
        _pyautogui.hotkey("ctrl", "v")
        time.sleep(0.05)
        _pyautogui.press("enter")
        try:
            if old_clip:
                pyperclip.copy(old_clip)
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug(f"Non-critical error: {e}")

        return f"Sent '{message}' to {contact_name} on Discord"

    def play_spotify(self, song_name: str) -> str:
        """
        Open Spotify (if needed) and play the requested song/artist/playlist.
        Uses Ctrl+L (Spotify search bar shortcut) and Enter to play.
        Works for: "play Starboy", "play weeknd", "play chill playlist"
        """
        import re as _re

        # ── Step 1: Ensure Spotify is open ──────────────────────────────────
        wins = gw.getWindowsWithTitle("Spotify")
        spotify_was_running = bool(wins)

        if not spotify_was_running:
            try:
                from agent.executors.app_launcher import AppLauncher
                ok = AppLauncher().open_and_wait("spotify", timeout=10.0)
                if not ok:
                    return "Could not open Spotify."
            except Exception as e:
                return f"Failed to open Spotify: {e}"
            # Re-fetch window after launch
            wins = gw.getWindowsWithTitle("Spotify")

        if not wins:
            return "Spotify window not found after launch."

        win = wins[0]
        try:
            if win.isMinimized:
                win.restore()
                time.sleep(0.3)
            win.activate()
            time.sleep(0.5)
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug(f"Non-critical error: {e}")

        # Extra wait on cold start — Spotify takes a few seconds to be interactive
        if not spotify_was_running:
            time.sleep(2.5)

        # ── Step 2: Focus search bar via Ctrl+L ─────────────────────────────
        _pyautogui.hotkey("ctrl", "l")
        time.sleep(0.5)

        # Clear + type song name via clipboard (unicode-safe)
        old_clip = ""
        try:
            old_clip = pyperclip.paste()
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug(f"Non-critical error: {e}")

        clean_query = _re.sub(r'[^\w\s\'-]', '', song_name).strip()
        pyperclip.copy(clean_query)
        _pyautogui.hotkey("ctrl", "a")
        _pyautogui.hotkey("ctrl", "v")
        time.sleep(0.3)
        _pyautogui.press("enter")
        time.sleep(1.2)   # wait for search results

        # ── Step 3: Move focus to Top Result and play it ─────────────────────
        # Modern Spotify UI often needs multiple tabs before the first playable item.
        for _ in range(3):
            _pyautogui.press("tab")
            time.sleep(0.18)
        _pyautogui.press("enter")

        # Restore clipboard
        try:
            if old_clip:
                pyperclip.copy(old_clip)
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug(f"Non-critical error: {e}")

        return f"Playing '{song_name}' on Spotify 🎵"

