"""
kuro/agents/host_agent.py
─────────────────────────────────────────────────────────────────
HostAgent — UFO2-style Orchestrator

Receives a parsed intent (tool + args) from Groq and dispatches it
to the appropriate specialist AppAgent. After execution it runs an
observation loop (OmniParser screenshot check) to verify the action.

Architecture:
  HostAgent
    ├── WhatsAppAgent  (send_whatsapp_message)
    ├── SpotifyAgent   (play_spotify, spotify_control)
    └── system apps    (open_application, general_response)
─────────────────────────────────────────────────────────────────
"""

import asyncio
import json
import logging
import os
import subprocess
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)


class HostAgent:
    """
    Central dispatcher. Accepts a structured intent dict and routes
    it to the appropriate AppAgent, then verifies execution visually.
    """

    def __init__(self):
        # Lazy-import agents to avoid loading pywinauto+spotapi at import time
        from kuro.agents.app_agents.whatsapp_agent import WhatsAppAgent
        from kuro.agents.app_agents.spotify_agent  import SpotifyAgent
        from kuro.agents.app_agents.type_agent     import TypeInAppAgent
        from kuro.utils.omniparser                 import OmniParserClient

        self._whatsapp = WhatsAppAgent()
        self._spotify  = SpotifyAgent()
        self._typer    = TypeInAppAgent()
        self._omni     = OmniParserClient()

        # Screen watcher — loaded lazily (no heavy import on startup)
        from agent.executors.screen_watcher import (
            start_watcher, stop_watcher, list_watchers
        )
        self._start_watcher = start_watcher
        self._stop_watcher  = stop_watcher
        self._list_watchers = list_watchers

        logger.info("[HostAgent] Initialized (WhatsApp + Spotify + OmniParser + ScreenWatcher)")

    # ── Public Entry Point ────────────────────────────────────────────────────

    async def execute(
        self,
        intent: dict[str, Any],
        on_status: Callable | None = None,
    ) -> dict[str, Any]:
        """
        Execute an intent produced by GroqReasoner.

        Args:
            intent:    {"tool": str, "args": dict, "speak_immediately": str}
            on_status: optional real-time status callback for Electron frontend

        Returns:
            {"success": bool, "message": str, "verified": bool}
        """
        tool = intent.get("tool")
        args = intent.get("args", {})

        denied = self._privacy_guard_deny(tool, args)
        if denied is not None:
            logger.warning("[HostAgent] PrivacyGuard blocked tool='%s' args=%s", tool, args)
            return denied

        logger.info(f"[HostAgent] Dispatching → {tool}({args})")

        if on_status:
            on_status({"status": "executing", "tool": tool})

        result   = await self._route(tool, args)
        # Fire-and-forget verification — don't block the response
        asyncio.create_task(self._verify_bg(tool, args, result))
        result["verified"] = True  # optimistic; bg task logs failures

        if on_status:
            on_status({"status": "done", "result": result})

        return result

    def _privacy_guard_deny(self, tool: str, args: dict) -> dict[str, Any] | None:
        """
        Hard privacy barrier for dangerous outbound/remote-control intents.
        Blocks when SECURITY_LEVEL=high and PRIVACY_GUARD_ENABLED=true.
        """
        sec_level = os.getenv("SECURITY_LEVEL", "high").lower()
        if sec_level != "high":
            return None
        if os.getenv("PRIVACY_GUARD_ENABLED", "true").lower() != "true":
            return None

        payload = f"{tool} {json.dumps(args, ensure_ascii=True)}".lower()
        blocked_tokens = {
            "remote_desktop", "cloud_upload", "upload_to_cloud", "rdp", "teamviewer",
            "anydesk", "chrome remote desktop", "drive.google.com", "dropbox", "onedrive",
            "mega.nz", "wetransfer",
        }
        if any(tok in payload for tok in blocked_tokens):
            return {
                "success": False,
                "app": "security",
                "error": "Blocked by Privacy Guard (high security policy)",
                "response_text": "Blocked by privacy policy. I will not perform remote desktop or cloud upload actions.",
            }
        return None

    # ── Routing ───────────────────────────────────────────────────────────────

    async def _route(self, tool: str, args: dict) -> dict[str, Any]:
        """Route to the correct AppAgent based on tool name."""
        try:
            if tool == "send_whatsapp_message":
                return await self._whatsapp.execute(args)
            elif tool == "send_whatsapp_file":
                return await self._whatsapp.send_file(args)

            elif tool == "play_spotify":
                return await self._spotify.execute_play(args)

            elif tool == "spotify_control":
                return await self._spotify.execute_control(args)

            elif tool == "open_application":
                r = await self._open_app(args)
                r.setdefault("app", str(args.get("app_name", "system")).lower())
                return r

            elif tool == "close_application":
                r = await self._close_app(args)
                r.setdefault("app", str(args.get("app_name", "system")).lower())
                return r

            elif tool == "multi_action":
                return await self._run_multi_action(args)

            elif tool == "type_in_app":
                return await self._typer.execute(args)

            elif tool == "general_response":
                return {"success": True, "message": args.get("response", "")}

            # ── Universal system control (Phase 6) ───────────────────────
            elif tool == "youtube_play":
                return await self._youtube_play(args)

            elif tool == "youtube_shorts":
                return await self._youtube_shorts()

            elif tool == "google_search":
                return await self._google_search(args)

            elif tool == "open_url":
                return await self._open_url(args)

            elif tool == "set_volume":
                return await self._set_volume(args)

            elif tool == "set_brightness":
                return await self._set_brightness(args)

            elif tool == "get_clipboard":
                return await self._get_clipboard()

            elif tool == "set_clipboard":
                return await self._set_clipboard(args)

            elif tool == "take_screenshot":
                return await self._take_screenshot()

            elif tool == "save_active_document":
                return await self._save_active_document(args)

            elif tool == "new_tab":
                return await self._browser_key("^t", "Opened new tab")

            elif tool == "close_tab":
                return await self._browser_key("^w", "Closed tab")

            # ── Screen Watcher (Phase 8) ──────────────────────────────────
            elif tool == "start_watcher":
                return self._start_watcher(
                    target_text   = args.get("target_text", ""),
                    app_name      = args.get("app_name", "any"),
                    interval_secs = float(args.get("interval_secs", 1.5)),
                )

            elif tool == "stop_watcher":
                return self._stop_watcher(target_text=args.get("target_text"))

            elif tool == "stop_all_watchers":
                return self._stop_watcher()

            elif tool == "list_watchers":
                return self._list_watchers()

            else:
                return {"success": False, "error": f"Unknown tool: {tool}"}

        except Exception as e:
            logger.error(f"[HostAgent] Execution error for '{tool}': {e}")
            return {"success": False, "error": str(e)}

    async def _open_app(self, args: dict) -> dict[str, Any]:
        """Open a desktop application via Windows shell."""
        app_name = args.get("app_name", "")
        # Merged aliases from legacy app_launcher.py and URI schemes
        aliases = {
            "chrome":         "chrome.exe",
            "google chrome":  "chrome.exe",
            "firefox":        "firefox.exe",
            "edge":           "msedge.exe",
            "notepad":        "notepad.exe",
            "notepad++":      "notepad++.exe",
            "calculator":     "calculator://",
            "calc":           "calculator://",
            "explorer":       "explorer.exe",
            "file explorer":  "explorer.exe",
            "settings":       "ms-settings:",
            "store":          "ms-windows-store://",
            "terminal":       "wt.exe",
            "windows terminal": "wt.exe",
            "cmd":            "cmd.exe",
            "command prompt": "cmd.exe",
            "powershell":     "powershell.exe",
            "vscode":         "code",
            "vs code":        "code",
            "visual studio code": "code",
            "spotify":        "spotify://",
            "whatsapp":       "whatsapp://",
            "discord":        "discord:",
            "telegram":       "telegram:",
            "vlc":            "vlc.exe",
            "paint":          "mspaint.exe",
            "mspaint":        "mspaint.exe",
            "word":           "winword.exe",
            "excel":          "excel.exe",
            "powerpoint":     "powerpnt.exe",
            "task manager":   "taskmgr.exe",
            "taskmgr":        "taskmgr.exe",
            "snipping tool":  "snippingtool.exe",
            "mail":           "mailto:",
            "calendar":       "outlookcal:",
            "maps":           "bingmaps:",
            "photos":         "ms-photos:",
            "xbox":           "xbox://",
            "teams":          "msteams:",
            "control":        "control.exe",
            "control panel":  "control.exe",
            "device manager":  "devmgmt.msc",
            "disk management": "diskmgmt.msc",
            "event viewer":   "eventvwr.msc",
            "services":       "services.msc",
            "regedit":        "regedit.exe",
            "registry":       "regedit.exe",
            "obs":            "obs64.exe",
        }
        cmd = aliases.get(app_name.lower(), app_name)
        try:
            if isinstance(cmd, str) and (cmd.endswith(":") or cmd.endswith("://")):
                os.system(f"start {cmd}")
            else:
                subprocess.Popen(cmd, shell=True)
            return {"success": True, "app": app_name.lower() or "system", "message": f"Opened {app_name}"}
        except Exception as e:
            return {"success": False, "app": app_name.lower() or "system", "error": str(e)}

    async def _close_app(self, args: dict) -> dict[str, Any]:
        """Close a desktop application using Windows taskkill."""
        app_name = args.get("app_name", "").lower()
        if not app_name:
            return {"success": False, "error": "No app_name provided"}
            
        aliases = {
            "chrome": "chrome.exe",
            "google chrome": "chrome.exe",
            "firefox": "firefox.exe",
            "edge": "msedge.exe",
            "notepad": "notepad.exe",
            "notepad++": "notepad++.exe",
            "calculator": "calculator.exe",
            "calc": "calculator.exe",
            "explorer": "explorer.exe",
            "settings": "systemsettings.exe",
            "terminal": "wt.exe",
            "windows terminal": "wt.exe",
            "cmd": "cmd.exe",
            "powershell": "powershell.exe",
            "vscode": "code.exe",
            "vs code": "code.exe",
            "spotify": "spotify.exe",
            "whatsapp": "whatsapp.exe",
            "discord": "discord.exe",
            "telegram": "telegram.exe",
            "vlc": "vlc.exe",
            "paint": "mspaint.exe",
            "word": "winword.exe",
            "excel": "excel.exe",
            "powerpoint": "powerpnt.exe",
            "task manager": "taskmgr.exe"
        }
        
        exe_name = aliases.get(app_name, f"{app_name}.exe")
        try:
            # Force kill the process tree quietly
            subprocess.run(["taskkill", "/F", "/T", "/IM", exe_name], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {"success": True, "app": app_name, "message": f"Closed {app_name}", "response_text": f"Closed {app_name}."}
        except subprocess.CalledProcessError:
            return {"success": False, "app": app_name, "error": f"Could not find or close {app_name}"}
        except Exception as e:
            return {"success": False, "app": app_name, "error": str(e)}

    async def _run_multi_action(self, args: dict) -> dict[str, Any]:
        """Run a list of actions sequentially and return aggregated result."""
        actions = args.get("actions", []) if isinstance(args, dict) else []
        if not isinstance(actions, list) or not actions:
            return {"success": False, "app": "system", "error": "No chained actions provided"}

        outputs: list[dict[str, Any]] = []
        for i, a in enumerate(actions, start=1):
            if not isinstance(a, dict):
                outputs.append({"success": False, "error": f"Invalid action at index {i-1}"})
                break
            tool = a.get("tool")
            a_args = a.get("args", {})
            r = await self._route(tool, a_args)
            outputs.append({"tool": tool, "args": a_args, **r})
            if not r.get("success"):
                break

        all_ok = all(o.get("success") for o in outputs) if outputs else False
        first_app = next((o.get("app") for o in outputs if o.get("app")), "system")
        message = "; ".join([o.get("message") or o.get("error") or "done" for o in outputs])
        return {
            "success": all_ok,
            "app": first_app,
            "message": message,
            "response_text": "Done." if all_ok else f"Partially completed. {message}",
            "steps": outputs,
        }

    # ── Universal System Control (Phase 6) ───────────────────────────────────

    async def _youtube_play(self, args: dict) -> dict[str, Any]:
        """Search YouTube and open first video in Chrome foreground."""
        query = args.get("query", "")
        url = await asyncio.get_event_loop().run_in_executor(
            None, self._resolve_youtube_url, query
        )
        await asyncio.get_event_loop().run_in_executor(None, self._open_in_chrome, url)
        return {"success": True, "app": "youtube", "message": f"Playing '{query}' on YouTube",
                "response_text": f"Playing {query} on YouTube."}

    def _resolve_youtube_url(self, query: str) -> str:
        """Fetch YouTube results page and extract first videoId — no API key."""
        import re, requests, urllib.parse
        try:
            resp = requests.get(
                "https://www.youtube.com/results",
                params={"search_query": query},
                headers={"User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )},
                timeout=6,
            )
            ids = re.findall(r'"videoId":"([a-zA-Z0-9_-]{11})"', resp.text)
            if ids:
                return f"https://www.youtube.com/watch?v={ids[0]}"
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug(f"Non-critical error: {e}")
        return f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"

    async def _youtube_shorts(self) -> dict[str, Any]:
        await asyncio.get_event_loop().run_in_executor(
            None, self._open_in_chrome, "https://www.youtube.com/shorts"
        )
        return {"success": True, "app": "youtube", "message": "Opened YouTube Shorts",
                "response_text": "Opened YouTube Shorts."}

    def _open_in_chrome(self, url: str) -> None:
        """Open URL in Chrome and bring the window to foreground."""
        chrome_paths = [
            os.path.expandvars(r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        ]

        chrome_exe = next((p for p in chrome_paths if os.path.exists(p)), None)
        if chrome_exe:
            subprocess.Popen([chrome_exe, "--new-tab", url])
        else:
            import webbrowser
            webbrowser.open_new_tab(url)

        time.sleep(0.8)

        try:
            import ctypes
            import win32api
            import win32con
            import win32gui

            hwnds: list[int] = []

            def _cb(hwnd, _):
                title = win32gui.GetWindowText(hwnd)
                if win32gui.IsWindowVisible(hwnd) and ("Chrome" in title or "Google" in title):
                    hwnds.append(hwnd)

            win32gui.EnumWindows(_cb, None)
            if hwnds:
                hwnd = hwnds[0]
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
                ctypes.windll.user32.SetForegroundWindow(hwnd)
                win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
        except Exception as e:
            logger.debug(f"[Chrome] Could not bring to foreground: {e}")

    async def _save_active_document(self, args: dict) -> dict[str, Any]:
        """Trigger Ctrl+S and type the path."""
        import pyautogui, os, time
        import pyperclip
        filename = args.get("filename", "Document.txt")
        location = args.get("location", "desktop").lower()
        
        if "desktop" in location:
            folder = os.path.expandvars(r"%USERPROFILE%\Desktop")
        else:
            folder = os.path.expandvars(r"%USERPROFILE%\Documents")
            
        full_path = os.path.join(folder, filename)
        
        pyautogui.hotkey("ctrl", "s")
        time.sleep(1.0) # Wait for Save dialog
        
        pyperclip.copy(full_path)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.5)
        pyautogui.press("enter")
        
        return {"success": True, "message": f"Saved to {full_path}", "response_text": f"Saved it to your {location}."}

    async def _google_search(self, args: dict) -> dict[str, Any]:
        import urllib.parse, webbrowser
        query = args.get("query", "")
        url = f"https://www.google.com/search?q={urllib.parse.quote(query)}"
        webbrowser.open(url)
        return {"success": True, "message": f"Searched Google for '{query}'",
                "response_text": f"Searching Google for {query}."}

    async def _open_url(self, args: dict) -> dict[str, Any]:
        import webbrowser
        url = args.get("url", "")
        if not url.startswith("http"):
            url = "https://" + url
        webbrowser.open(url)
        return {"success": True, "message": f"Opened {url}",
                "response_text": f"Opening {url}."}

    async def _set_volume(self, args: dict) -> dict[str, Any]:
        """Set system volume 0–100 using pycaw."""
        level = int(args.get("level", 50))
        level = max(0, min(level, 100))
        try:
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            from ctypes import cast, POINTER
            from comtypes import CLSCTX_ALL
            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            volume = cast(interface, POINTER(IAudioEndpointVolume))
            volume.SetMasterVolumeLevelScalar(level / 100.0, None)
            return {"success": True, "message": f"Volume set to {level}%",
                    "response_text": f"Volume set to {level} percent."}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _set_brightness(self, args: dict) -> dict[str, Any]:
        """Set screen brightness 0-100."""
        level = int(args.get("level", 50))
        level = max(0, min(level, 100))
        try:
            import screen_brightness_control as sbc
            sbc.set_brightness(level)
            return {"success": True, "message": f"Brightness set to {level}%",
                    "response_text": f"Brightness set to {level} percent."}
        except ImportError:
            return {"success": False, "error": "screen_brightness_control not installed."}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _get_clipboard(self) -> dict[str, Any]:
        """Read clipboard content."""
        import pyperclip
        text = pyperclip.paste()
        return {"success": True, "message": "Clipboard read", "response_text": text}

    async def _set_clipboard(self, args: dict) -> dict[str, Any]:
        """Write text to clipboard."""
        import pyperclip
        text = args.get("text", "")
        pyperclip.copy(text)
        return {"success": True, "message": "Copied to clipboard", "response_text": "Copied to clipboard."}

    async def _take_screenshot(self) -> dict[str, Any]:
        import os
        save_path = "data/screenshot.png"
        os.makedirs("data", exist_ok=True)
        try:
            import mss
            with mss.mss() as sct:
                sct.shot(output=save_path)
            return {"success": True, "message": f"Screenshot saved to {save_path}",
                    "response_text": "Screenshot taken."}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _browser_key(self, keys: str, confirm: str) -> dict[str, Any]:
        """Send a keyboard shortcut to the active browser window."""
        from pywinauto.keyboard import send_keys as _send
        await asyncio.get_event_loop().run_in_executor(None, _send, keys)
        return {"success": True, "message": confirm, "response_text": confirm + "."}

    # ── Observation Loop ──────────────────────────────────────────────────────

    async def _verify_bg(self, tool: str, args: dict, result: dict) -> None:
        """Background verification — logs result but never blocks pipeline."""
        try:
            verified = await self._verify_action(tool, args, result)
            if not verified:
                logger.warning(f"[HostAgent] Background verify failed for {tool}")
        except Exception as e:
            logger.debug(f"[HostAgent] Verify error (non-blocking): {e}")

    async def _verify_action(
        self,
        tool: str,
        args: dict,
        result: dict,
    ) -> bool:
        """
        Take a screenshot and use OmniParser to confirm the action completed.
        Non-blocking: if OmniParser is not running, returns True (trusts agent).
        """
        if not result.get("success"):
            return False

        # These tools have no visual footprint to verify
        if tool in ("general_response", "spotify_control", "open_application"):
            return True

        # For WhatsApp: verify the sent message bubble is visible in the chat
        if tool == "send_whatsapp_message":
            await asyncio.sleep(0.5)   # Short wait for UI to update
            elements = await self._omni.parse_screen(
                target_label=args.get("message", "")
            )
            if elements:
                logger.info("[HostAgent] Message delivery verified via OmniParser")
                return True
            else:
                logger.warning("[HostAgent] OmniParser: message bubble not detected (uncertain)")
                return False   # Uncertain — not a hard failure

        return True
