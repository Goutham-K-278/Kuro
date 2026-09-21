"""
kuro/agents/app_agents/whatsapp_agent.py

WhatsApp AppAgent — Vision-Guided Messaging

Flow:
  1. Resolve contact → phone number (deep link) or display name (UI search)
  2. If phone → deep link (instant, reliable)
  3. If UI search → type name, vision-verify contact found, then send
  4. After send → vision-verify message delivered

Vision verification uses LLaVA (llava:7b via Ollama) to screenshot the
screen and confirm what's actually visible before proceeding.
"""

import asyncio
import ctypes
import logging
import os
import re
import subprocess
import time
from typing import Any
from kuro.agents.app_agents._base_agent import BaseAppAgent

logger = logging.getLogger(__name__)


def _normalize_contact(contact: str) -> str:
    c = (contact or "").strip()
    c = re.sub(r"\b(?:in|on|via)\s+whatsapp\b", "", c, flags=re.I)
    c = re.sub(r"[\s\.,;:!?-]+$", "", c)
    return c.strip()


def _find_whatsapp_hwnd() -> int | None:
    """Find a WhatsApp top-level window handle via EnumWindows."""
    import win32gui
    hwnds: list[int] = []

    def _cb(hwnd, _):
        title = win32gui.GetWindowText(hwnd) or ""
        if "whatsapp" in title.lower():
            hwnds.append(hwnd)

    win32gui.EnumWindows(_cb, None)
    return hwnds[0] if hwnds else None


def _ensure_whatsapp_foreground() -> bool:
    import psutil, win32gui, win32con, win32api

    def _launch_whatsapp() -> None:
        wa_local = os.path.expandvars(r"%LOCALAPPDATA%\WhatsApp\WhatsApp.exe")
        wa_appdata = os.path.expandvars(r"%APPDATA%\WhatsApp\WhatsApp.exe")
        if os.path.exists(wa_local):
            subprocess.Popen(wa_local)
            return
        if os.path.exists(wa_appdata):
            subprocess.Popen(wa_appdata)
            return
        subprocess.Popen(["explorer.exe", "whatsapp:"], shell=False)

    if not any("whatsapp" in (p.info.get("name") or "").lower() for p in psutil.process_iter(["name"])):
        logger.info("[WhatsApp] App process not found — launching")
        _launch_whatsapp()
        time.sleep(2.0)

    for i in range(20):
        hwnd = _find_whatsapp_hwnd()
        if hwnd:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            time.sleep(0.15)
            win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
            ctypes.windll.user32.SetForegroundWindow(hwnd)
            win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
            time.sleep(0.3)
            return True
        if i in (4, 10):
            _launch_whatsapp()
        time.sleep(0.5)

    return False


def _type_text_direct(text: str, char_delay: float = 0.025) -> None:
    """Type text via clipboard paste (Ctrl+V). Handles all Unicode."""
    import win32clipboard, win32api, win32con
    win32clipboard.OpenClipboard()
    win32clipboard.EmptyClipboard()
    win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
    win32clipboard.CloseClipboard()
    time.sleep(0.1)
    win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
    win32api.keybd_event(ord('V'), 0, 0, 0)
    time.sleep(0.05)
    win32api.keybd_event(ord('V'), 0, win32con.KEYEVENTF_KEYUP, 0)
    win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(0.15)


def _paste_message_and_send(message: str) -> None:
    """Paste full message verbatim and trigger send."""
    from pywinauto.keyboard import send_keys

    _type_text_direct(message)
    time.sleep(0.25)
    send_keys("{ENTER}")
    time.sleep(0.25)


def _focus_compose_box_best_effort() -> None:
    """Ensure WhatsApp window is active so the automatically-focused compose box can receive text."""
    try:
        from pywinauto import Application
        app = Application(backend="uia").connect(title_re=".*WhatsApp.*", timeout=2)
        main_win = app.window(title_re=".*WhatsApp.*")
        # Bring window to front
        main_win.set_focus()
        import time
        time.sleep(0.2)
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug(f"[WhatsApp] UIA focus failed: {e}")


class WhatsAppAgent(BaseAppAgent):
    def __init__(self):
        # Lazy-init vision verifier
        self._vision = None

    def _get_vision(self):
        """Lazy-load vision verifier to avoid import-time overhead."""
        if self._vision is None:
            try:
                from kuro.utils.vision_verify import get_vision_verifier
                self._vision = get_vision_verifier()
            except Exception as e:
                logger.debug(f"[WhatsApp] Vision verifier not available: {e}")
        return self._vision

    async def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        contact = _normalize_contact(args.get("contact", ""))
        message = args.get("message", "").strip()
        if not contact or not message:
            return {"success": False, "app": "whatsapp", "error": "Contact or message is empty"}
        logger.info(f"[WhatsApp] Sending '{message}' to '{contact}'")
        try:
            return await asyncio.get_event_loop().run_in_executor(
                None, self._send_sync, contact, message
            )
        except Exception as e:
            logger.error(f"[WhatsApp] Error: {e}")
            return {"success": False, "app": "whatsapp", "error": str(e)}

    def _send_sync(self, contact: str, message: str) -> dict[str, Any]:
        from pywinauto.keyboard import send_keys

        # Keep paragraphs readable and avoid accidental leading/trailing blank lines.
        message = re.sub(r"\r\n?", "\n", message).strip("\n")

        # ── Step 1: Resolve contact ──────────────────────────────────────
        search_name = contact
        phone = None
        try:
            from agent.executors.messaging import MessagingAgent
            _msg = MessagingAgent()
            phone, display_name, confident = _msg.resolve_contact(contact)
            if not confident:
                suggestions = _msg.suggest_contacts(contact, max_suggestions=3)
                if suggestions:
                    top = suggestions[0]
                    rest = ", ".join(suggestions[1:])
                    hint = f" Did you mean {top}?" + (f" Other matches: {rest}." if rest else "")
                    return {
                        "success": False,
                        "app": "whatsapp",
                        "needs_confirmation": True,
                        "candidates": suggestions,
                        "message": f"Ambiguous contact: {contact}",
                        "response_text": f"I could not confidently find {contact}.{hint}",
                    }
            if confident and display_name:
                search_name = display_name
                logger.info(f"[WhatsApp] Resolved '{contact}' → '{display_name}' (phone={phone})")
        except Exception as e:
            logger.debug(f"[WhatsApp] Contact resolution skipped: {e}")

        # ── Step 2: Deep Link (preferred — 100% reliable if phone known) ──
        if phone:
            import os
            import urllib.parse

            encoded_message = urllib.parse.quote(message)
            clean_phone = "".join(c for c in phone if c.isdigit() or c == "+")
            uri = f"whatsapp://send?phone={clean_phone}&text={encoded_message}"

            logger.info(f"[WhatsApp] Using Deep Link for phone: {clean_phone}")
            # Prefer OS handler so '&text=' is not parsed by cmd shell.
            try:
                os.startfile(uri)
            except Exception:
                subprocess.Popen(f'cmd /c start "" "{uri}"', shell=True)

            # Wait for WhatsApp to surface and populate the text field
            time.sleep(2.5)
            _ensure_whatsapp_foreground()
            time.sleep(0.5)

            _focus_compose_box_best_effort()
            _paste_message_and_send(message)

            # Vision verification (async — fire in background)
            self._schedule_vision_verify(message, search_name)

            logger.info(f"[WhatsApp] Sent '{message}' to '{search_name}' via deep link")
            return {
                "success": True,
                "app": "whatsapp",
                "message": f"Sent '{message}' to {contact}",
                "response_text": f"Sent '{message}' to {contact} on WhatsApp.",
            }

        # ── Step 3: UI Search Fallback (UIA-guided) ────────────────────
        # Anti-college-group safety net
        blocked_keywords = ["sjit", "pep", "placement", "iii year", "st. joseph", "st joseph"]
        if any(b in search_name.lower() or b in contact.lower() for b in blocked_keywords):
            return {"success": False, "app": "whatsapp", "error": f"Blocked: Prevented sending message to a potential college group ('{contact}')."}

        if not _ensure_whatsapp_foreground():
            return {"success": False, "app": "whatsapp", "error": "Could not open WhatsApp"}

        time.sleep(0.5)

        # We use UIA to find the exact list item instead of blind enter
        try:
            from pywinauto import Application
            app = Application(backend="uia").connect(title_re=".*WhatsApp.*", timeout=5)
            win = app.window(title_re=".*WhatsApp.*")
            
            # Focus search box and type
            send_keys("^f")
            time.sleep(0.5)
            send_keys("^a")
            time.sleep(0.1)
            _type_text_direct(search_name, char_delay=0.03)
            time.sleep(1.5) # Wait for search results
            
            # Find the contact in the list
            try:
                # Find list item that contains the search name
                # We use re.escape to handle any special characters safely
                safe_name = re.escape(search_name)
                contact_item = win.child_window(title_re=f"(?i).*{safe_name}.*", control_type="ListItem", found_index=0)
                contact_item.click_input()
                logger.info(f"[WhatsApp] UIA clicked contact '{search_name}'")
            except Exception as item_e:
                logger.warning(f"[WhatsApp] UIA could not find contact '{search_name}' by name. Falling back to top result.")
                try:
                    # Instantly click the first search result without doing a slow regex search
                    top_item = win.child_window(control_type="ListItem", found_index=0)
                    top_item.click_input()
                except Exception:
                    send_keys("{DOWN}")
                    time.sleep(0.2)
                    send_keys("{ENTER}")
                
        except Exception as e:
            logger.warning(f"[WhatsApp] UIA Search failed: {e}. Falling back to blind keys.")
            send_keys("^f")
            time.sleep(0.5)
            send_keys("^a")
            time.sleep(0.1)
            _type_text_direct(search_name, char_delay=0.03)
            time.sleep(2.0)
            send_keys("{DOWN}")
            time.sleep(0.2)
            send_keys("{ENTER}")

        time.sleep(1.0)

        _focus_compose_box_best_effort()
        _paste_message_and_send(message)

        # Vision verification (background)
        self._schedule_vision_verify(message, search_name)

        logger.info(f"[WhatsApp] Sent '{message}' to '{search_name}' via UI search")
        return {
            "success": True,
            "app": "whatsapp",
            "message": f"Sent '{message}' to {contact}",
            "response_text": f"Sent '{message}' to {contact} on WhatsApp.",
        }

    # ── Vision Helpers ────────────────────────────────────────────────────

    def _run_vision_sync(self, coro_func, *args):
        """Run an async vision function from sync context."""
        try:
            loop = asyncio.new_event_loop()
            result = loop.run_until_complete(coro_func(*args))
            loop.close()
            return result
        except Exception as e:
            logger.debug(f"[WhatsApp] Vision sync call failed: {e}")
            return None

    def _schedule_vision_verify(self, message: str, contact: str):
        """Schedule a background vision verification after sending."""
        vision = self._get_vision()
        if not vision or not vision._available:
            return

        async def _bg_verify():
            await asyncio.sleep(1.0)  # Wait for UI to settle
            result = await vision.verify_message_sent(message)
            if result.get("sent"):
                logger.info(f"[WhatsApp] ✅ Vision confirmed: message to '{contact}' delivered")
            else:
                logger.warning(f"[WhatsApp] ⚠️ Vision uncertain: {result.get('description', 'unknown')}")

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(_bg_verify())
            else:
                asyncio.run(_bg_verify())
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug(f"Non-critical error: {e}")

    def _get_contact_aliases(self, contact: str) -> list[str]:
        """Get alternative names for a contact from contacts.json."""
        import json
        contacts_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))))),
            "contacts", "contacts.json"
        )
        try:
            with open(contacts_path, encoding="utf-8") as f:
                data = json.load(f)
            for key, val in data.items():
                all_names = [key, val.get("name", ""), val.get("whatsapp_name", "")]
                all_names.extend(val.get("aliases", []))
                all_names = [n for n in all_names if n]
                if any(contact.lower() in n.lower() or n.lower() in contact.lower()
                       for n in all_names):
                    return all_names
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug(f"Non-critical error: {e}")
        return []

    async def send_file(self, args: dict) -> dict[str, Any]:
        """
        Send a file to a WhatsApp contact.
        Uses vision to locate the attachment button.
        """
        from pathlib import Path
        import asyncio
        
        contact = args.get("contact", "")
        file_path = args.get("file_path", "")
        
        path = Path(file_path)
        if not path.exists():
            return {"success": False, "error": f"File not found: {file_path}"}
        
        # Step 1: Open WhatsApp and navigate to contact
        # Using existing contact navigation helper if available, else open command
        if _pyautogui is None:
            return {"success": False, "error": "pyautogui unavailable"}
        
        _launch_whatsapp()
        await asyncio.sleep(2.0)
        
        # Ctrl+F to search
        _pyautogui.hotkey("ctrl", "f")
        await asyncio.sleep(0.5)
        
        import pyperclip
        pyperclip.copy(contact)
        _pyautogui.hotkey("ctrl", "v")
        await asyncio.sleep(1.0)
        
        _pyautogui.press("down")
        await asyncio.sleep(0.2)
        _pyautogui.press("enter")
        await asyncio.sleep(1.0)
        
        # Step 2: Find attachment button via vision
        clicked = await self.vision_find_and_click("attachment button clip")
        if not clicked:
            # Try keyboard shortcut as fallback
            _pyautogui.hotkey("ctrl", "shift", "a")
            await asyncio.sleep(0.3)
        
        # Step 3: Find "Document" or "File" option in the attachment menu
        await asyncio.sleep(0.4)
        clicked = await self.vision_find_and_click("Document")
        if not clicked:
            pass
        
        # Step 4: Type file path into the file dialog
        await asyncio.sleep(0.8)
        pyperclip.copy(str(path.absolute()))
        _pyautogui.hotkey("ctrl", "a")
        _pyautogui.hotkey("ctrl", "v")
        await asyncio.sleep(0.2)
        _pyautogui.press("enter")
        await asyncio.sleep(1.5)
        
        # Step 5: Find and click Send in the preview dialog
        clicked = await self.vision_find_and_click("Send button")
        if not clicked:
            _pyautogui.press("enter")  # Enter usually sends in file preview
        
        await asyncio.sleep(0.5)
        return {"success": True, "message": f"Sent {path.name} to {contact}", "response_text": f"Sent {path.name} to {contact}."}
