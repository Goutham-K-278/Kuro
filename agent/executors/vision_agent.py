# agent/executors/vision_agent.py
"""
Vision Agent — Screen-aware interaction for Kuro.

Execution modes (fastest → slowest):
  1. UIA (Windows accessibility tree) — ~500ms, 95% reliable
  2. Spotify API (spotipy) — ~300ms, 99% reliable
  3. LLaVA vision (llava:7b via Ollama) — ~4-6s, 80% reliable

Backward-compatible: retains find_and_click, click_at, analyze_screen,
read_screen_and_act for existing action_router.py calls.
"""

import os, sys, time, logging, base64, re, io, json

logger = logging.getLogger("kuro.vision")

# ── Optional imports (graceful degradation) ─────────────────────────────────

_uia = None
try:
    import uiautomation as _uia
except ImportError:
    logger.info("uiautomation not installed — UIA fast-path disabled")

_mss = None
try:
    import mss as _mss
except ImportError:
    logger.info("mss not installed — screen capture disabled")

_pyautogui = None
try:
    import pyautogui as _pyautogui
    _pyautogui.FAILSAFE = True
    _pyautogui.PAUSE = 0.05
except ImportError:
    logger.info("pyautogui not installed — mouse/keyboard disabled")

_pyperclip = None
try:
    import pyperclip as _pyperclip
except ImportError:
    logger.info("pyperclip not installed — clipboard paste disabled")

_httpx = None
try:
    import httpx as _httpx
except ImportError:
    logger.info("httpx not installed — Ollama vision calls disabled")


# ── Singleton ───────────────────────────────────────────────────────────────

_instance = None

def get_vision_agent():
    """Return the module-level VisionAgent singleton."""
    global _instance
    if _instance is None:
        _instance = VisionAgent()
    return _instance


# ── VisionAgent ─────────────────────────────────────────────────────────────

class VisionAgent:
    def __init__(self):
        self._uia_available = _uia is not None
        self._mss_available = _mss is not None
        self._pyautogui_available = _pyautogui is not None
        self._vision_available = False
        self._vision_model = None
        self._spotify = None

        # Check for vision model in Ollama
        self._init_vision_model()
        # Init Spotify API (optional)
        self._init_spotify()

        logger.info(
            f"VisionAgent ready — UIA={self._uia_available}, "
            f"MSS={self._mss_available}, LLaVA={self._vision_available}"
        )

    # ── Init helpers ────────────────────────────────────────────────────

    def _init_vision_model(self):
        """Check if a vision-capable model (LLaVA, etc.) is available in Ollama."""
        if _httpx is None:
            return
        try:
            r = _httpx.get("http://localhost:11434/api/tags", timeout=3.0)
            models = [m["name"] for m in r.json().get("models", [])]
            for name in models:
                if any(v in name.lower() for v in ("llava", "bakllava", "minicpm-v", "moondream")):
                    self._vision_model = name
                    self._vision_available = True
                    logger.info(f"Vision LLM available: {name}")
                    return
            logger.info("No vision LLM in Ollama — vision fallback disabled. "
                        "Run: ollama pull llava:7b")
        except Exception as e:
            logger.warning(f"Ollama not reachable: {e}")

    def _init_spotify(self):
        """Initialize Spotify API client if credentials are available."""
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass

        client_id = os.getenv("SPOTIFY_CLIENT_ID", "")
        client_secret = os.getenv("SPOTIFY_CLIENT_SECRET", "")
        if not (client_id and client_secret):
            logger.info("Spotify API credentials not set — Spotify control disabled")
            return

        try:
            import spotipy
            from spotipy.oauth2 import SpotifyOAuth
            self._spotify = spotipy.Spotify(auth_manager=SpotifyOAuth(
                client_id=client_id,
                client_secret=client_secret,
                redirect_uri="http://localhost:8888/callback",
                scope="user-modify-playback-state user-read-playback-state",
                open_browser=False,
                cache_path=".spotify_cache",
            ))
            logger.info("Spotify API client initialized")
        except Exception as e:
            logger.warning(f"Spotify init failed: {e}")

    # ── Core: Screenshot ────────────────────────────────────────────────

    def _capture_screen_b64(self) -> str:
        """Capture screen via mss and return as base64-encoded PNG string."""
        if not self._mss_available:
            raise RuntimeError("mss not installed — cannot capture screen")
        import mss
        import mss.tools
        with mss.mss() as sct:
            monitor = sct.monitors[1]  # primary monitor
            shot = sct.grab(monitor)
            png_bytes = mss.tools.to_png(shot.rgb, shot.size)
        return base64.b64encode(png_bytes).decode("utf-8")

    def take_screenshot(self):
        """Capture the current screen. Returns a PIL Image (backward compat)."""
        if _pyautogui:
            return _pyautogui.screenshot()
        # Fallback to mss + PIL
        if self._mss_available:
            import mss
            from PIL import Image
            with mss.mss() as sct:
                shot = sct.grab(sct.monitors[1])
                return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        raise RuntimeError("No screenshot method available")

    def screenshot_to_base64(self, img=None) -> str:
        """Convert a PIL screenshot to base64 (backward compat)."""
        if img is None:
            return self._capture_screen_b64()
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("utf-8")

    # ── Vision LLM helpers ──────────────────────────────────────────────

    def _ask_llava(self, prompt: str, screenshot_b64: str = None) -> str:
        """Send a prompt (+ optional screenshot) to the vision model via Ollama."""
        if not self._vision_available or _httpx is None:
            return ""
        if screenshot_b64 is None:
            screenshot_b64 = self._capture_screen_b64()

        payload = {
            "model": self._vision_model,
            "prompt": prompt,
            "images": [screenshot_b64],
            "stream": False,
            "options": {"temperature": 0, "num_predict": 256},
        }
        try:
            r = _httpx.post(
                "http://localhost:11434/api/generate",
                json=payload,
                timeout=30.0,
            )
            return r.json().get("response", "").strip()
        except Exception as e:
            logger.error(f"LLaVA call failed: {e}")
            return ""

    # ── Main dispatcher ─────────────────────────────────────────────────

    def execute(self, intent: dict) -> str:
        """
        Execute a vision intent.
        Supported actions: send_message, play_media, vision_click, vision_instruction.
        """
        action = intent.get("action", "")

        if action == "send_message":
            contact = intent.get("contact", intent.get("target", ""))
            message = intent.get("message", intent.get("content", ""))
            if not contact or not message:
                return "Missing contact or message."
            return self._whatsapp_send(contact, message)

        if action == "play_media":
            query = intent.get("query", intent.get("content", ""))
            if not query:
                return "What would you like me to play?"
            return self._spotify_play(query)

        if action == "vision_click":
            target = intent.get("target", intent.get("content", ""))
            if target:
                return self._vision_find_and_click(target)
            return "What should I click?"

        if action == "vision_instruction":
            instruction = intent.get("instruction", intent.get("content", ""))
            if instruction:
                return self.execute_instruction(instruction)
            return "What would you like me to do?"

        return f"Unknown vision action: {action}"

    # ── WhatsApp: UIA fast-path ─────────────────────────────────────────

    def _whatsapp_send(self, contact: str, message: str) -> str:
        """Send a WhatsApp message using Windows UIA accessibility tree."""
        if not self._uia_available:
            return self._whatsapp_send_vision(contact, message)

        try:
            # Find WhatsApp window
            wa = _uia.WindowControl(searchDepth=2, Name="WhatsApp")
            if not wa.Exists(maxSearchSeconds=3):
                # Try to launch WhatsApp
                os.startfile("whatsapp:")
                time.sleep(3)
                wa = _uia.WindowControl(searchDepth=2, Name="WhatsApp")
                if not wa.Exists(maxSearchSeconds=3):
                    return "Could not find WhatsApp window."

            wa.SetFocus()
            time.sleep(0.3)

            # Click search box (Ctrl+F or look for search element)
            if _pyautogui:
                _pyautogui.hotkey("ctrl", "f")
                time.sleep(0.5)

                # Type contact name
                if _pyperclip:
                    _pyperclip.copy(contact)
                    _pyautogui.hotkey("ctrl", "v")
                else:
                    _pyautogui.typewrite(contact, interval=0.03)
                time.sleep(1.0)

                # Press Down + Enter to select first result
                _pyautogui.press("down")
                time.sleep(0.2)
                _pyautogui.press("enter")
                time.sleep(0.5)

                # Find message input and type message
                # Click on message input area (usually at bottom)
                msg_box = wa.EditControl(searchDepth=8, AutomationId="InputBarTextArea")
                if msg_box.Exists(maxSearchSeconds=2):
                    msg_box.Click()
                else:
                    # Fallback: press Tab to reach message input
                    _pyautogui.press("tab")
                time.sleep(0.3)

                # Type message using clipboard for Unicode support
                if _pyperclip:
                    _pyperclip.copy(message)
                    _pyautogui.hotkey("ctrl", "v")
                else:
                    _pyautogui.typewrite(message, interval=0.02)
                time.sleep(0.2)

                # Press Enter to send
                _pyautogui.press("enter")
                time.sleep(0.3)

                return f"Sent '{message}' to {contact} on WhatsApp."
            else:
                return "pyautogui not available for keyboard input."

        except Exception as e:
            logger.warning(f"WhatsApp UIA path failed: {e}")
            return self._whatsapp_send_vision(contact, message)

    def _whatsapp_send_vision(self, contact: str, message: str) -> str:
        """Fallback: send WhatsApp message using vision (LLaVA)."""
        if not self._vision_available:
            return f"Cannot send message — UIA and vision both unavailable."

        steps = [
            f"1. Find and click the search box in WhatsApp",
            f"2. Type '{contact}' in the search box",
            f"3. Click on the contact '{contact}' in the results",
            f"4. Find the message input box at the bottom",
            f"5. Type '{message}' in the message box",
            f"6. Click the send button or press Enter",
        ]
        return self.execute_instruction("\n".join(steps))

    # ── Spotify: API path ───────────────────────────────────────────────

    def _spotify_play(self, query: str) -> str:
        """Play a track on Spotify via API, with UIA/vision fallback."""
        # Try Spotify API first
        if self._spotify:
            try:
                results = self._spotify.search(q=query, type="track", limit=1)
                tracks = results.get("tracks", {}).get("items", [])
                if tracks:
                    track = tracks[0]
                    track_name = track["name"]
                    artist = track["artists"][0]["name"]
                    uri = track["uri"]

                    # Try to play on active device
                    devices = self._spotify.devices()
                    device_list = devices.get("devices", [])
                    if device_list:
                        device_id = device_list[0]["id"]
                        self._spotify.start_playback(
                            device_id=device_id,
                            uris=[uri],
                        )
                        return f"Playing '{track_name}' by {artist} on Spotify."
                    else:
                        return (f"Found '{track_name}' by {artist} but no active Spotify device. "
                                f"Open Spotify and play something first to activate a device.")
                else:
                    return f"No tracks found for '{query}' on Spotify."
            except Exception as e:
                logger.warning(f"Spotify API error: {e}")
                # Fall through to UIA/vision fallback

        # Fallback: try UIA automation on Spotify app
        return self._spotify_play_uia(query)

    def _spotify_play_uia(self, query: str) -> str:
        """Fallback: play on Spotify via UIA or keyboard shortcuts."""
        if not _pyautogui:
            return f"Cannot play '{query}' — no Spotify API or automation available."

        try:
            # Try to find Spotify window
            if self._uia_available:
                sp = _uia.WindowControl(searchDepth=2, Name="Spotify")
                if not sp.Exists(maxSearchSeconds=2):
                    # Try to launch
                    os.startfile("spotify:")
                    time.sleep(3)
                    sp = _uia.WindowControl(searchDepth=2, Name="Spotify")

                if sp.Exists(maxSearchSeconds=2):
                    sp.SetFocus()
                    time.sleep(0.3)

            # Use Ctrl+L to focus search, type query
            _pyautogui.hotkey("ctrl", "l")
            time.sleep(0.5)

            if _pyperclip:
                _pyperclip.copy(query)
                _pyautogui.hotkey("ctrl", "v")
            else:
                _pyautogui.typewrite(query, interval=0.03)
            time.sleep(1.5)

            # Press Enter to search, then Enter again to play first result
            _pyautogui.press("enter")
            time.sleep(1.0)
            _pyautogui.press("enter")
            time.sleep(0.5)

            return f"Searching and playing '{query}' on Spotify."
        except Exception as e:
            logger.error(f"Spotify UIA fallback failed: {e}")
            return f"Could not play '{query}' on Spotify: {e}"

    # ── Vision: LLaVA-guided click ──────────────────────────────────────

    def _vision_find_and_click(self, description: str) -> str:
        """Use LLaVA to find a UI element and click it."""
        if not self._vision_available:
            # Fallback to OCR-style find
            return self.find_and_click(description)

        screenshot_b64 = self._capture_screen_b64()
        prompt = (
            f"I need to click on '{description}'. "
            f"Look at the screenshot and tell me the approximate pixel coordinates (x, y) "
            f"of the center of '{description}'. "
            f"Reply with ONLY the coordinates in format: x,y  "
            f"For example: 450,320"
        )
        response = self._ask_llava(prompt, screenshot_b64)

        # Parse coordinates from response
        coord_match = re.search(r'(\d+)\s*,\s*(\d+)', response)
        if coord_match and _pyautogui:
            x, y = int(coord_match.group(1)), int(coord_match.group(2))
            _pyautogui.click(x, y)
            time.sleep(0.3)
            return f"Clicked on '{description}' at ({x}, {y})."

        return f"Could not locate '{description}' on screen. LLaVA response: {response[:100]}"

    # ── Screen reading ──────────────────────────────────────────────────

    def read_screen_text(self) -> str:
        """Read and describe what's on the screen using LLaVA or UIA."""
        if self._vision_available:
            return self._ask_llava(
                "Describe what you see on the screen. Be concise. "
                "List the main window, visible text, and any notable UI elements."
            )

        # Fallback: UIA tree dump
        if self._uia_available:
            try:
                root = _uia.GetRootControl()
                lines = []
                child = root.GetFirstChildControl()
                count = 0
                while child and count < 10:
                    name = child.Name or "(no name)"
                    ctrl_type = child.ControlTypeName or "?"
                    lines.append(f"{ctrl_type}: {name}")
                    child = child.GetNextSiblingControl()
                    count += 1
                return "Visible windows:\n" + "\n".join(lines)
            except Exception as e:
                logger.error(f"UIA tree dump failed: {e}")

        return "Cannot read screen — no vision model or UIA available."

    # ── Multi-step instruction execution ────────────────────────────────

    def execute_instruction(self, instruction: str) -> str:
        """
        Execute a multi-step instruction using LLaVA for planning.
        LLaVA analyzes the screen and generates step-by-step actions.
        """
        if not self._vision_available:
            return f"Cannot execute instruction — vision model not available."

        screenshot_b64 = self._capture_screen_b64()
        prompt = (
            f"I need to: {instruction}\n\n"
            f"Look at the screenshot. Give me a numbered list of specific actions "
            f"to accomplish this task. For each action, specify one of:\n"
            f"- CLICK x,y  (click at pixel coordinates)\n"
            f"- TYPE text   (type this text)\n"
            f"- KEY key     (press this key, e.g. enter, tab)\n"
            f"- WAIT secs   (wait this many seconds)\n\n"
            f"Be precise with coordinates. Reply with ONLY the action list."
        )
        plan = self._ask_llava(prompt, screenshot_b64)

        if not plan:
            return "Vision model returned empty plan."

        # Execute each step
        results = []
        for line in plan.strip().split("\n"):
            line = line.strip()
            if not line:
                continue

            # Parse action from line
            click_m = re.search(r'CLICK\s+(\d+)\s*,\s*(\d+)', line, re.IGNORECASE)
            type_m = re.search(r'TYPE\s+(.+)', line, re.IGNORECASE)
            key_m = re.search(r'KEY\s+(\w+)', line, re.IGNORECASE)
            wait_m = re.search(r'WAIT\s+([\d.]+)', line, re.IGNORECASE)

            if click_m and _pyautogui:
                x, y = int(click_m.group(1)), int(click_m.group(2))
                _pyautogui.click(x, y)
                results.append(f"Clicked ({x},{y})")
                time.sleep(0.3)
            elif type_m and _pyautogui:
                text = type_m.group(1).strip()
                if _pyperclip:
                    _pyperclip.copy(text)
                    _pyautogui.hotkey("ctrl", "v")
                else:
                    _pyautogui.typewrite(text, interval=0.02)
                results.append(f"Typed '{text[:30]}'")
                time.sleep(0.2)
            elif key_m and _pyautogui:
                key = key_m.group(1).lower()
                _pyautogui.press(key)
                results.append(f"Pressed {key}")
                time.sleep(0.2)
            elif wait_m:
                secs = min(float(wait_m.group(1)), 5.0)
                time.sleep(secs)
                results.append(f"Waited {secs}s")

        return f"Executed {len(results)} steps: {'; '.join(results)}" if results else f"No actionable steps found in plan."

    # ── Backward-compatible methods (used by action_router.py) ──────────

    def find_and_click(self, search_text: str, click_offset_x: int = 0, click_offset_y: int = 0) -> str:
        """Find text on screen and click it. Uses vision or UIA."""
        # Try vision first
        if self._vision_available:
            return self._vision_find_and_click(search_text)
        # Fallback: use Florence-2 for text-based screen grounding
        try:
            import asyncio
            loop = asyncio.new_event_loop()
            result = loop.run_until_complete(self.find_and_click_text(search_text))
            loop.close()
            if result:
                return f"Clicked on '{search_text}'."
        except Exception as e:
            logger.warning(f"find_and_click_text failed: {e}")
        return f"Could not find '{search_text}' on screen."

    async def find_and_click_text(self, search_text: str) -> bool:
        """Find text on screen using Florence-2 OCR and click it."""
        from kuro.vision.florence_engine import FlorenceEngine
        
        screen = self._capture_screen()  # MSS screenshot -> numpy array
        engine = FlorenceEngine.get_instance()
        engine.ensure_loaded()
        
        # Use Florence-2 phrase grounding: find bounding box of text
        result = await engine.phrase_grounding(screen, search_text)
        
        if result and result.get("boxes"):
            box = result["boxes"][0]  # [x1, y1, x2, y2] normalized
            h, w = screen.shape[:2]
            cx = int((box[0] + box[2]) / 2 * w)
            cy = int((box[1] + box[3]) / 2 * h)
            if _pyautogui:
                _pyautogui.click(cx, cy)
            return True
        
        logger.warning(f"Could not find '{search_text}' on screen via Florence-2")
        return False

    def click_at(self, x: int, y: int) -> str:
        """Click at exact screen coordinates."""
        if _pyautogui:
            _pyautogui.click(x, y)
            return f"Clicked at ({x}, {y})."
        return "pyautogui not available."

    def double_click_at(self, x: int, y: int) -> str:
        if _pyautogui:
            _pyautogui.doubleClick(x, y)
            return f"Double-clicked at ({x}, {y})."
        return "pyautogui not available."

    def analyze_screen(self, prompt: str = "Describe what you see on the screen.") -> str:
        """Send a screenshot to the vision LLM for analysis."""
        if self._vision_available:
            return self._ask_llava(prompt)
        return self.read_screen_text()

    def read_screen_and_act(self, command: str) -> str:
        """Vision fallback for unknown commands."""
        cmd_lower = command.lower().strip()

        # "find X" / "click X"
        find_match = re.match(r'(?:find|click(?:\s+on)?|select|tap)\s+(.+)', cmd_lower)
        if find_match:
            target = find_match.group(1).strip()
            return self._vision_find_and_click(target) if self._vision_available else self.find_and_click(target)

        # "scroll down/up"
        scroll_match = re.match(r'scroll\s+(up|down)(?:\s+(\d+))?', cmd_lower)
        if scroll_match and _pyautogui:
            direction = scroll_match.group(1)
            clicks = int(scroll_match.group(2) or 3)
            amount = clicks if direction == "up" else -clicks
            _pyautogui.scroll(amount)
            return f"Scrolled {direction} {abs(clicks)} times."

        # If we have vision, ask it what to do
        if self._vision_available:
            analysis = self._ask_llava(
                f"I need to: {command}. What UI element should I click or what action "
                f"should I take? Give coordinates if possible."
            )
            return f"Screen analysis: {analysis}"

        return f"Could not execute '{command}' — no matching UI action found."
