# agent/executors/system_agent.py
import warnings
warnings.warn(
    "agent/executors/system_agent.py is deprecated. Functionality moved to kuro/agents/host_agent.py. "
    "This module will be removed in Phase 6.",
    DeprecationWarning,
    stacklevel=2
)
import subprocess, os, ctypes, time
import psutil
import logging
logger = logging.getLogger(__name__)

try:
    import pyautogui as _pyautogui
    PYAUTOGUI_AVAILABLE = True
except ImportError:
    _pyautogui = None
    PYAUTOGUI_AVAILABLE = False

try:
    import pygetwindow as gw
    PYGETWINDOW_AVAILABLE = True
except ImportError:
    gw = None
    PYGETWINDOW_AVAILABLE = False

# ── Window focus tracking ─────────────────────────────────────────────────────
# Set by main.py right before the wake/listen call so we know which window
# was focused when the user spoke. type_text() uses this to restore focus.
_target_window_title: str | None = None

def set_target_window(title: str | None):
    """Called by main.py to record which window was active before Kuro overlay."""
    global _target_window_title
    _target_window_title = title

def _refocus_target_window():
    """
    Bring the previously active (target) window back into focus.
    Falls back to the most recently used non-Kuro visible window.
    """
    if not PYGETWINDOW_AVAILABLE: return False
    global _target_window_title
    try:
        # Try to refocus the exact window that was active before Kuro
        if _target_window_title:
            matches = [
                w for w in gw.getAllWindows()
                if w.visible and w.title and
                _target_window_title.lower() in w.title.lower()
            ]
            if matches:
                win = matches[0]
                if win.isMinimized:
                    win.restore()
                win.activate()
                time.sleep(0.3)
                return True

        # Fallback: focus the first visible non-Kuro window
        KURO_KEYWORDS = ('kuro', 'electron', 'kuro ai')
        candidates = [
            w for w in gw.getAllWindows()
            if w.visible and w.title and
            not any(k in w.title.lower() for k in KURO_KEYWORDS)
        ]
        if candidates:
            win = candidates[0]
            if win.isMinimized:
                win.restore()
            win.activate()
            time.sleep(0.3)
            return True
    except Exception as e:
        pass  # silently ignore window focus errors
    return False

class SystemAgent:
	# ── Audio ────────────────────────────────────────────────────
	def set_volume(self, level: int) -> str:
		from ctypes import cast, POINTER
		from comtypes import CLSCTX_ALL
		from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
		devices   = AudioUtilities.GetSpeakers()
		interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
		volume    = cast(interface, POINTER(IAudioEndpointVolume))
		volume.SetMasterVolumeLevelScalar(max(0.0, min(1.0, level/100)), None)
		return f"Volume set to {level}%."

	def mute(self) -> str:
		if not PYAUTOGUI_AVAILABLE:
		    logger.warning("pyautogui not available")
		    return "Error: pyautogui unavailable"
		_pyautogui.press('volumemute')
		return "Muted."

	def volume_up(self, steps=5) -> str:
		if not PYAUTOGUI_AVAILABLE:
		    logger.warning("pyautogui not available")
		    return "Error: pyautogui unavailable"
		for _ in range(steps): _pyautogui.press('volumeup')
		return f"Volume increased by {steps}."

	def volume_down(self, steps=5) -> str:
		if not PYAUTOGUI_AVAILABLE:
		    logger.warning("pyautogui not available")
		    return "Error: pyautogui unavailable"
		for _ in range(steps): _pyautogui.press('volumedown')
		return f"Volume decreased by {steps}."

	# ── Display ──────────────────────────────────────────────────
	def set_brightness(self, level: int) -> str:
		try:
			import screen_brightness_control as sbc
		except ImportError:
			return "screen_brightness_control not installed. Run: pip install screen-brightness-control"
		sbc.set_brightness(level)
		return f"Brightness set to {level}%."

	def get_brightness(self) -> str:
		try:
			import screen_brightness_control as sbc
		except ImportError:
			return "screen_brightness_control not installed."
		level = sbc.get_brightness()[0]
		return f"Current brightness: {level}%."

	# ── Power ────────────────────────────────────────────────────
	def shutdown(self, delay: int = 30) -> str:
		os.system(f"shutdown /s /t {delay}")
		return f"Shutting down in {delay} seconds."

	def restart(self, delay: int = 5) -> str:
		os.system(f"shutdown /r /t {delay}")
		return f"Restarting in {delay} seconds."

	def cancel_shutdown(self) -> str:
		os.system("shutdown /a")
		return "Shutdown/restart cancelled."

	def hibernate(self) -> str:
		os.system("shutdown /h")
		return "Hibernating."

	def sleep(self) -> str:
		subprocess.Popen("rundll32.exe powrprof.dll,SetSuspendState 0,1,0", shell=True)
		return "Going to sleep."

	def lock_screen(self) -> str:
		ctypes.windll.user32.LockWorkStation()
		return "Screen locked."

	# ── Screenshots & Clipboard ──────────────────────────────────
	def screenshot(self, save_path: str = None) -> str:
		from datetime import datetime
		if not save_path:
			save_path = os.path.expanduser(
				f"~/Desktop/kuro_shot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")
		_pyautogui.screenshot(save_path)
		return f"Screenshot saved: {os.path.basename(save_path)}"

	def get_clipboard(self) -> str:
		import pyperclip
		return pyperclip.paste()

	def set_clipboard(self, text: str) -> str:
		import pyperclip
		pyperclip.copy(text)
		return "Copied to clipboard."

	# ── System Info ──────────────────────────────────────────────
	def get_battery(self) -> str:
		bat = psutil.sensors_battery()
		if bat:
			status = "charging 🔌" if bat.power_plugged else "on battery 🔋"
			mins_left = ""
			if not bat.power_plugged and bat.secsleft > 0:
				mins_left = f", ~{bat.secsleft//60} min remaining"
			return f"Battery: {bat.percent:.0f}% {status}{mins_left}"
		return "No battery detected."

	def get_stats(self) -> str:
		cpu  = psutil.cpu_percent(interval=0.5)
		ram  = psutil.virtual_memory()
		disk = psutil.disk_usage('C:\\')
		net  = psutil.net_io_counters()
		return (
			f"CPU: {cpu}%  |  "
			f"RAM: {ram.percent}% ({ram.used//1024//1024}MB/{ram.total//1024//1024}MB)  |  "
			f"Disk C: {disk.percent}% used  |  "
			f"Net ↑{net.bytes_sent//1024//1024}MB ↓{net.bytes_recv//1024//1024}MB"
		)

	def list_processes(self, sort_by="cpu", top=10) -> str:
		procs = []
		for p in psutil.process_iter(['name','cpu_percent','memory_info','pid']):
			try:
				procs.append({
					'name': p.info['name'],
					'cpu': p.info['cpu_percent'],
					'mem': p.info['memory_info'].rss//1024//1024,
					'pid': p.info['pid']
				})
			except Exception as e:
			    import logging
			    logging.getLogger(__name__).debug(f"Non-critical error: {e}")
		procs.sort(key=lambda x: x[sort_by if sort_by in ("cpu","mem") else "cpu"], reverse=True)
		result = f"Top {top} processes by {sort_by}:\n"
		for p in procs[:top]:
			result += f"  {p['name']:<30} CPU:{p['cpu']:>5}%  RAM:{p['mem']:>5}MB  PID:{p['pid']}\n"
		return result

	def kill_process(self, name_or_pid) -> str:
		killed = 0
		for proc in psutil.process_iter(['name','pid']):
			try:
				if (isinstance(name_or_pid, int) and proc.info['pid'] == name_or_pid) or \
				   (isinstance(name_or_pid, str) and name_or_pid.lower() in proc.info['name'].lower()):
					proc.kill()
					killed += 1
			except Exception as e:
			    import logging
			    logging.getLogger(__name__).debug(f"Non-critical error: {e}")
		return f"Killed {killed} process(es) matching '{name_or_pid}'."

	def run_command(self, cmd: str, capture=True) -> str:
		result = subprocess.run(cmd, shell=True, capture_output=capture, text=True, timeout=15)
		out = (result.stdout or "") + (result.stderr or "")
		return out.strip()[:500] if out.strip() else "Command executed."

	def open_url(self, url: str) -> str:
		import webbrowser
		if not url.startswith("http"): url = "https://" + url
		webbrowser.open(url)
		return f"Opened {url}."

	# ── Typing ───────────────────────────────────────────────────

	# Keywords that signal the user wants *generated* content, not literal text
	_GENERATE_KEYWORDS = (
		"program", "code", "function", "script", "algorithm", "implementation",
		"class", "method", "loop", "sort", "binary search",
		"linked list", "stack", "queue", "tree", "graph", "fibonacci",
		"factorial", "prime", "palindrome", "reverse",
		"api", "server", "database", "sql", "html", "css",
		"essay", "email", "letter", "paragraph", "story", "poem",
		"summary", "report", "template", "readme", "documentation",
	)
	# Language hints — if user says "in python", "in c++", etc., it's generative
	_LANG_HINTS = (
		"in python", "in c++", "in c", "in java", "in javascript", "in js",
		"in rust", "in go", "in typescript", "in ts", "in ruby", "in swift",
		"in kotlin", "in c#", "in csharp", "in php", "in html",
	)

	def _is_generative(self, text: str) -> bool:
		"""Decide if 'type X' means 'generate X' or 'literally type X'."""
		t = text.lower()
		# Short literal text (≤ 3 words, no gen keywords) → literal
		if len(t.split()) <= 3 and not any(k in t for k in self._GENERATE_KEYWORDS):
			return False
		# Language hint present → definitely generative
		if any(h in t for h in self._LANG_HINTS):
			return True
		# Generation keyword present → generative
		if any(k in t for k in self._GENERATE_KEYWORDS):
			return True
		# Longer text without keywords → literal dictation
		return False

	def _detect_language(self, text: str) -> str:
		"""Extract the programming language from the request, default to python."""
		t = text.lower()
		for hint in self._LANG_HINTS:
			if hint in t:
				lang = hint.replace("in ", "").strip()
				# Normalize
				return {"js": "javascript", "ts": "typescript", "csharp": "c#"}.get(lang, lang)
		# Check for standalone mentions
		for lang in ("python", "c++", "java", "javascript", "c", "rust", "go"):
			if lang in t:
				return lang
		return "python"  # default

	def smart_type(self, content: str) -> str:
		"""
		Intelligent typing:
		- "type hi" → literally types 'hi'
		- "type binary search program in c++" → generates code via LLM, streams it
		- "type an email to john about the meeting" → generates email, types it
		First token appears in ~0.5-1s thanks to Ollama streaming.
		"""
		if not content:
			return "Nothing to type."

		if self._is_generative(content):
			return self._stream_type_from_llm(content)
		else:
			return self.type_text(content)

	def _stream_type_from_llm(self, description: str) -> str:
		"""
		Generate content via Ollama and stream-type it into the active window.
		First characters appear in ~0.5-1s. Full program typed in real-time.
		"""
		import pyperclip, logging
		# Save current clipboard to restore after streaming
		try:
			_saved_clipboard = pyperclip.paste()
		except Exception:
			_saved_clipboard = ""
		try:
			import ollama as _ollama
		except ImportError:
			return "Ollama not installed. Cannot generate content."

		logger = logging.getLogger("kuro.smart_type")

		# Detect language for code requests
		lang = self._detect_language(description)
		# Build a focused prompt
		is_code = any(k in description.lower() for k in (
			"program", "code", "function", "script", "algorithm", "class",
			"sort", "search", "linked list", "stack", "queue", "tree",
			"fibonacci", "factorial", "prime", "palindrome", "hello world",
			"api", "server", "loop",
		))

		if is_code:
			prompt = (
				f"Write a {description} in {lang}. "
				f"Output ONLY the code with comments. No explanation, no markdown fences, no ```."
			)
		else:
			prompt = (
				f"Write the following: {description}. "
				f"Output ONLY the text. No explanation, no markdown formatting."
			)

		# Refocus the target window BEFORE we start streaming
		_refocus_target_window()
		time.sleep(0.3)

		# Stream from Ollama — type each chunk as it arrives
		model = os.getenv("LOCAL_MODEL", "gemma4:e4b")
		total_chars = 0
		buffer = ""  # accumulate small chunks before pasting
		FLUSH_SIZE = 12  # paste every ~12 chars for smooth typing effect

		try:
			import requests
			import json
			url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434") + "/api/chat"
			payload = {
				"model": model,
				"messages": [{"role": "user", "content": prompt}],
				"stream": True,
				"options": {"temperature": 0.2, "num_predict": 1024, "num_ctx": 2048},
			}
			
			resp = requests.post(url, json=payload, stream=True, timeout=15.0)
			resp.raise_for_status()

			for line in resp.iter_lines():
				if not line:
					continue
				chunk = json.loads(line)
				token = chunk.get("message", {}).get("content", "")
				if not token:
					continue
				buffer += token
				total_chars += len(token)

				# Flush buffer when big enough or contains newline
				if len(buffer) >= FLUSH_SIZE or "\n" in buffer:
					pyperclip.copy(buffer)
					_pyautogui.hotkey('ctrl', 'v')
					time.sleep(0.02)  # tiny delay to prevent paste overlap
					buffer = ""

			# Flush remaining
			if buffer:
				pyperclip.copy(buffer)
				_pyautogui.hotkey('ctrl', 'v')

			logger.info(f"Smart-typed {total_chars} chars via LLM stream")
			try: pyperclip.copy(_saved_clipboard)
			except Exception as e:
			    import logging
			    logging.getLogger(__name__).debug(f"Non-critical error: {e}")
			return f"Generated and typed {total_chars} characters ({description[:60]})"

		except Exception as e:
			logger.error(f"Stream-type failed: {e}")
			# If streaming failed mid-way, report what we got
			try: pyperclip.copy(_saved_clipboard)
			except Exception as e:
			    import logging
			    logging.getLogger(__name__).debug(f"Non-critical error: {e}")
			if total_chars > 0:
				return f"Partially typed {total_chars} chars (error: {e})"
			return f"Could not generate content: {e}"

	def type_text(self, text: str, delay_before: float = 0.4) -> str:
		"""
		Type literal text at the current cursor position.
		Uses clipboard paste (Ctrl+V) — Unicode-safe and ~10× faster than
		_pyautogui.typewrite().  Refocuses the previously active window before
		typing so text goes to the right application (e.g. Notepad), not Kuro.
		"""
		import pyperclip
		if not text:
			return "Nothing to type."
		_refocus_target_window()
		time.sleep(delay_before)
		pyperclip.copy(text)
		_pyautogui.hotkey('ctrl', 'v')
		time.sleep(0.1)
		return f"Typed: {text[:80]}{'...' if len(text) > 80 else ''}"

	def press_key(self, key: str) -> str:
		"""Press a single key or hotkey (e.g. 'enter', 'ctrl+s')."""
		if '+' in key:
			parts = [k.strip() for k in key.split('+')]
			_pyautogui.hotkey(*parts)
		else:
			_pyautogui.press(key.strip())
		return f"Pressed {key}."

	# ── Clipboard Intelligence ────────────────────────────────────────────

	def read_clipboard(self) -> str:
		"""Read and return current clipboard content."""
		import pyperclip
		content = pyperclip.paste()
		if not content:
			return "Clipboard is empty."
		preview = content[:200]
		suffix = "..." if len(content) > 200 else ""
		return f"Clipboard contains: {preview}{suffix}"

	def summarize_clipboard(self, llm_engine=None) -> str:
		"""Summarize clipboard content using the LLM."""
		import pyperclip
		content = pyperclip.paste()
		if not content:
			return "Clipboard is empty."
		if not llm_engine:
			return f"Clipboard: {content[:300]}"
		try:
			import ollama
			messages = [
				{"role": "system", "content": "Summarize the following text in 2-3 sentences. Be concise."},
				{"role": "user",   "content": content[:3000]},
			]
			resp = ollama.chat(model=os.getenv("LOCAL_MODEL", "gemma4:e4b"),
							   messages=messages, options={"temperature": 0})
			return resp["message"]["content"].strip()
		except Exception as e:
			return f"Could not summarize clipboard: {e}"

	def translate_clipboard(self, target_lang: str = "English", llm_engine=None) -> str:
		"""Translate clipboard content to target language."""
		import pyperclip
		content = pyperclip.paste()
		if not content:
			return "Clipboard is empty."
		try:
			import ollama
			messages = [
				{"role": "system", "content": f"Translate the following text to {target_lang}. Return only the translation."},
				{"role": "user",   "content": content[:3000]},
			]
			resp = ollama.chat(model=os.getenv("LOCAL_MODEL", "gemma4:e4b"),
							   messages=messages, options={"temperature": 0})
			return resp["message"]["content"].strip()
		except Exception as e:
			return f"Could not translate clipboard: {e}"

	# ── WiFi / Bluetooth Toggle ───────────────────────────────────────────

	def toggle_wifi(self, enable: bool = True) -> str:
		"""Enable or disable WiFi adapter via netsh."""
		action = "enable" if enable else "disable"
		try:
			result = subprocess.run(
				["netsh", "interface", "set", "interface", "Wi-Fi", action],
				capture_output=True, text=True, timeout=10,
			)
			if result.returncode == 0:
				return f"WiFi {'enabled' if enable else 'disabled'}."
			# Try alternate interface name
			result2 = subprocess.run(
				["netsh", "interface", "set", "interface", "WiFi", action],
				capture_output=True, text=True, timeout=10,
			)
			if result2.returncode == 0:
				return f"WiFi {'enabled' if enable else 'disabled'}."
			return f"WiFi toggle failed. Try manually. ({result.stderr.strip()})"
		except Exception as e:
			return f"Failed to toggle WiFi: {e}"

	def toggle_bluetooth(self, enable: bool = True) -> str:
		"""Enable or disable Bluetooth via PowerShell PnpDevice."""
		action = "Enable" if enable else "Disable"
		ps_script = (
			f"$radio = Get-PnpDevice | Where-Object {{$_.Class -eq 'Bluetooth' -and $_.Status -ne 'Unknown'}}; "
			f"if ($radio) {{ {action}-PnpDevice -InstanceId $radio.InstanceId -Confirm:$false }}"
		)
		try:
			subprocess.run(
				["powershell", "-Command", ps_script],
				capture_output=True, timeout=15,
			)
			return f"Bluetooth {'enabled' if enable else 'disabled'}."
		except Exception as e:
			return f"Failed to toggle Bluetooth: {e}"

	# ── Notepad (win32 backend — no screen coordinates) ──────────────────────

	def open_notepad_and_type(self, content: str, filename: str = None) -> dict:
		"""
		Opens Notepad and types content into it using pywinauto win32 backend.
		No screen coordinates — works at any resolution, any DPI.
		"""
		try:
			from pywinauto import Application
			from pywinauto.keyboard import send_keys
		except ImportError:
			return {"success": False, "error": "pywinauto not installed. Run: pip install pywinauto"}

		# Launch Notepad
		subprocess.Popen("notepad.exe")
		time.sleep(1.5)

		try:
			app = Application(backend="win32").connect(title_re=".*Notepad", timeout=8)
			notepad_win = app.window(title_re=".*Notepad")
			notepad_win.set_focus()

			# Get the edit control directly by class — no coordinates needed
			edit = notepad_win.child_window(class_name="Edit")
			edit.type_keys(content, with_spaces=True, pause=0.0)

			if filename:
				send_keys("^s")
				time.sleep(0.5)
				save_dialog = app.window(title_re="Save As")
				save_dialog.child_window(class_name="Edit").type_keys(
					filename, with_spaces=True
				)
				send_keys("{ENTER}")

			return {"success": True}
		except Exception as e:
			return {"success": False, "error": str(e)}

	def generate_code_in_notepad(self, language: str, description: str, code: str) -> dict:
		"""
		Opens Notepad and types pre-generated code (from LLM) into it.
		Args:
		    language:    Programming language (e.g. 'python', 'c++')
		    description: Short description for the filename
		    code:        The code string to type (pre-generated by LLM)
		"""
		import re
		clean_code = re.sub(r"^```[a-z]*\n?", "", code, flags=re.MULTILINE|re.IGNORECASE)
		clean_code = re.sub(r"\n?```$", "", clean_code, flags=re.MULTILINE)
		clean_code = clean_code.strip()
		
		header = f"// Language: {language}\n// Description: {description}\n\n"
		full_content = header + clean_code
		filename = f"{description.replace(' ', '_')}.{self._get_extension(language)}"
		return self.open_notepad_and_type(full_content, filename)

	@staticmethod
	def _get_extension(language: str) -> str:
		ext_map = {
			"c++": "cpp", "cpp": "cpp",
			"python": "py", "java": "java",
			"javascript": "js", "js": "js",
			"typescript": "ts", "c": "c",
			"html": "html", "css": "css",
			"c#": "cs", "csharp": "cs",
			"go": "go", "rust": "rs",
		}
		return ext_map.get(language.lower(), "txt")
