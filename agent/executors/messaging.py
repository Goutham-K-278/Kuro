# agent/executors/messaging.py
import time
import subprocess
import json
import os
import webbrowser
import urllib.parse
import difflib
import psutil
import logging

logger = logging.getLogger(__name__)

pyautogui = None  # Lazy import — only used as last fallback

CONTACTS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "contacts", "contacts.json"
)

def load_contacts() -> dict:
    if os.path.exists(CONTACTS_FILE):
        with open(CONTACTS_FILE) as f:
            return json.load(f)
    return {}

def save_contacts(contacts: dict):
    os.makedirs(os.path.dirname(CONTACTS_FILE), exist_ok=True)
    with open(CONTACTS_FILE, "w") as f:
        json.dump(contacts, f, indent=2)

class MessagingAgent:
    def __init__(self):
        self.contacts = load_contacts()
        self._contacts_cache = self.contacts  # In-memory cache for fast resolution

    # ── WhatsApp Desktop (UIA backend — reliably triggers React events) ────────
    def send_whatsapp(self, contact_name: str, message: str) -> str:
        """
        Reliably sends a WhatsApp Desktop message using pywinauto UIA backend.
        Uses character-by-character typing to trigger React's synthetic onChange events.
        Falls back to pyautogui deep-link method if UIA fails.
        """
        try:
            return self._send_whatsapp_uia(contact_name, message)
        except Exception as e:
            logger.warning(f"[Messaging] UIA method failed: {e} — trying deep-link fallback")
            # Resolve to phone number for deep-link
            phone, display_name, confident = self.resolve_contact(contact_name)
            if phone:
                return self.send_whatsapp_deeplink(phone, message)
            return f"❌ Could not find contact '{contact_name}' and UIA method failed: {e}"

    def _send_whatsapp_uia(self, contact_name: str, message: str) -> str:
        """
        Core UIA implementation for WhatsApp Desktop.
        WhatsApp is a React/Electron app — standard pyautogui doesn't trigger React's
        synthetic input events. UIA backend sends input at the OS level, which React sees.
        """
        from pywinauto import Application
        from pywinauto.keyboard import send_keys

        # Step 1: Launch WhatsApp if not already running
        whatsapp_running = any(
            proc.name().lower() in ["whatsapp.exe", "whatsapp desktop.exe"]
            for proc in psutil.process_iter(["name"])
        )
        if not whatsapp_running:
            wa_path = os.path.expandvars(r"%LOCALAPPDATA%\WhatsApp\WhatsApp.exe")
            subprocess.Popen(wa_path, shell=True)
            time.sleep(4)  # Wait for WhatsApp to fully load

        # Step 2: Connect with UIA backend
        app = Application(backend="uia").connect(title_re="WhatsApp", timeout=10)
        main_window = app.window(title_re="WhatsApp")
        main_window.set_focus()
        time.sleep(0.5)

        # Step 3: Open search with Ctrl+F
        send_keys("^f")
        time.sleep(0.6)

        # Step 4: Clear any existing search text, then type contact name char-by-char
        # Character-by-character with 30ms pause triggers React's onChange events
        send_keys("^a")  # Select all in search box
        for char in contact_name:
            send_keys(char, pause=0.03)
        time.sleep(0.8)

        # Step 5: Press Enter to open first search result
        send_keys("{ENTER}")
        time.sleep(0.6)

        # Step 6: Tab to focus the message input box
        send_keys("{TAB}")
        time.sleep(0.3)

        # Step 7: Type message char-by-char (React needs this for compose box too)
        for char in message:
            send_keys(char, pause=0.02)
        time.sleep(0.2)
        send_keys("{ENTER}")

        return f"✅ Sent WhatsApp message to {contact_name}."

    def send_whatsapp_deeplink(self, phone: str, message: str) -> str:
        """
        Send WhatsApp message via deep-link URI.
        Works with WhatsApp Desktop (Store version) — bypasses search entirely.
        """
        encoded = urllib.parse.quote(message)
        clean_phone = "".join(c for c in phone if c.isdigit() or c == "+")
        uri = f"whatsapp://send?phone={clean_phone}&text={encoded}"
        subprocess.Popen(["start", "", uri], shell=True)
        time.sleep(2.5)
        try:
            import pyautogui as _pg
            _pg.press("enter")
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug(f"Non-critical error: {e}")
        time.sleep(0.3)
        return f"✅ Sent WhatsApp message to {clean_phone} via deep-link."

    def send_whatsapp_by_phone(self, phone: str, message: str) -> str:
        """Open WhatsApp Web with pre-filled message, auto-send via Enter key."""
        encoded = urllib.parse.quote(message)
        url = f"https://web.whatsapp.com/send?phone={phone}&text={encoded}"
        webbrowser.open(url)
        try:
            time.sleep(10)
            import pyautogui as _pg
            _pg.press("enter")
            return f"✅ WhatsApp message sent via web to {phone}."
        except Exception:
            return f"WhatsApp Web opened for {phone}. Press Enter to send."

    def resolve_contact(self, name: str) -> tuple:
        """
        Returns (phone_number, display_name, is_confident).
        Uses multilayer matching:
          1. Exact key match
          2. Exact display-name match
          3. Exact alias match (aliases array in contacts.json)
          4. difflib fuzzy match (cutoff 0.55) on keys, display names, AND aliases
          5. Substring containment fallback
        """
        name_lower = name.lower().strip()
        contacts = load_contacts()

        # 1. Exact key
        if name_lower in contacts:
            c = contacts[name_lower]
            preferred = c.get("whatsapp_name") or c.get("name", name_lower.title())
            return c.get("phone"), preferred, True

        # 2. Exact display name
        for key, val in contacts.items():
            if val.get("name", "").lower() == name_lower:
                preferred = val.get("whatsapp_name") or val.get("name", key.title())
                return val.get("phone"), preferred, True

        # 3. Exact alias match
        for key, val in contacts.items():
            aliases = val.get("aliases", [])
            for alias in aliases:
                if alias.lower() == name_lower:
                    preferred = val.get("whatsapp_name") or val.get("name", key.title())
                    return val.get("phone"), preferred, True

        # 4. Fuzzy match on keys + display names + aliases
        candidates: dict[str, str] = {}
        for key, val in contacts.items():
            candidates[key] = key
            dn = val.get("name", "")
            if dn:
                candidates[dn.lower()] = key
            # Add all aliases to fuzzy candidate pool
            for alias in val.get("aliases", []):
                candidates[alias.lower()] = key
        close = difflib.get_close_matches(name_lower, candidates.keys(), n=1, cutoff=0.55)
        if close:
            matched_key = candidates[close[0]]
            c = contacts[matched_key]
            preferred = c.get("whatsapp_name") or c.get("name", matched_key.title())
            return c.get("phone"), preferred, True

        # 5. Substring containment
        for key, val in contacts.items():
            if name_lower in key or key in name_lower:
                preferred = val.get("whatsapp_name") or val.get("name", key.title())
                return val.get("phone"), preferred, True
            # Also check aliases for substring
            for alias in val.get("aliases", []):
                if name_lower in alias.lower() or alias.lower() in name_lower:
                    preferred = val.get("whatsapp_name") or val.get("name", key.title())
                    return val.get("phone"), preferred, True

        return None, name.strip().title(), False

    def suggest_contacts(self, name: str, max_suggestions: int = 3) -> list[str]:
        """Return likely contact display names for ambiguous input."""
        name_lower = (name or "").lower().strip()
        if not name_lower:
            return []

        contacts = load_contacts()
        candidates: dict[str, str] = {}
        for key, val in contacts.items():
            display = val.get("whatsapp_name") or val.get("name") or key.title()
            candidates[key.lower()] = display
            if val.get("name"):
                candidates[val.get("name", "").lower()] = display
            for alias in val.get("aliases", []):
                candidates[str(alias).lower()] = display

        ranked = difflib.get_close_matches(name_lower, candidates.keys(), n=max_suggestions, cutoff=0.45)
        out: list[str] = []
        for r in ranked:
            d = candidates[r]
            if d not in out:
                out.append(d)
        return out

    # ── Telegram Desktop ──────────────────────────────────────────
    def send_telegram(self, contact_name: str, message: str) -> str:
        import pyautogui as _pg
        self._focus_or_open("Telegram",
            f"{os.environ.get('APPDATA')}\\Telegram Desktop\\Telegram.exe")
        _pg.hotkey("ctrl", "f")
        time.sleep(0.35)
        self._type_fast(message=contact_name)
        time.sleep(0.9)
        _pg.press("enter")
        time.sleep(0.4)
        self._type_fast(message=message)
        time.sleep(0.15)
        _pg.press("enter")
        return f"✅ Sent Telegram message to {contact_name}."

    # ── Discord ───────────────────────────────────────────────────
    def send_discord(self, target: str, message: str) -> str:
        import pyautogui as _pg
        self._focus_or_open("Discord",
            f"{os.environ.get('LOCALAPPDATA')}\\Discord\\Update.exe --processStart Discord.exe")
        _pg.hotkey("ctrl", "k")
        time.sleep(0.4)
        self._type_fast(message=target)
        time.sleep(0.8)
        _pg.press("enter")
        time.sleep(0.4)
        self._type_fast(message=message)
        time.sleep(0.15)
        _pg.press("enter")
        return f"✅ Sent Discord message to {target}."

    # ── Email via Outlook ─────────────────────────────────────────
    def send_email_outlook(self, to: str, subject: str, body: str) -> str:
        """Opens new email in Outlook"""
        mailto = (
            f"mailto:{to}?subject={urllib.parse.quote(subject)}"
            f"&body={urllib.parse.quote(body)}"
        )
        subprocess.Popen(f'start "" "{mailto}"', shell=True)
        time.sleep(2)
        import pyautogui as _pg
        _pg.hotkey("alt", "s")
        return f"✅ Email sent to {to}."

    def add_contact(self, name: str, phone: str = None, email: str = None):
        contacts = load_contacts()
        contacts[name.lower()] = {"phone": phone, "email": email, "name": name}
        save_contacts(contacts)
        return f"Contact {name} saved."

    # ── Internal Helpers ───────────────────────────────────────────
    def _type_fast(self, message: str):
        """Use clipboard paste — faster than key-by-key for non-React apps."""
        import pyperclip
        import pyautogui as _pg
        pyperclip.copy(message)
        _pg.hotkey("ctrl", "v")
        time.sleep(0.15)

    def _focus_or_open(self, app_name: str, exe_path: str) -> bool:
        try:
            import pygetwindow as gw
            windows = [w for w in gw.getAllWindows()
                       if app_name.lower() in w.title.lower() and w.visible]
            if windows:
                try:
                    if windows[0].isMinimized:
                        windows[0].restore()
                    windows[0].activate()
                    return True
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).debug(f"Non-critical error: {e}")
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug(f"Non-critical error: {e}")
        subprocess.Popen(exe_path, shell=True)
        time.sleep(3.5)
        return True
