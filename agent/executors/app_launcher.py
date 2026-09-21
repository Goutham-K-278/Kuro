# agent/executors/app_launcher.py
import warnings
warnings.warn(
    "agent/executors/app_launcher.py is deprecated. Functionality moved to kuro/agents/host_agent.py. "
    "This module will be removed in Phase 6.",
    DeprecationWarning,
    stacklevel=2
)
import subprocess, os, time, glob
try:
    import pygetwindow as gw
    PYGETWINDOW_AVAILABLE = True
except ImportError:
    gw = None
    PYGETWINDOW_AVAILABLE = False
import psutil

USERNAME     = os.environ.get("USERNAME", "User")
APPDATA      = os.environ.get("APPDATA", "")
LOCALAPPDATA = os.environ.get("LOCALAPPDATA", "")
# ── URI Schemes — instant launch for Store/modern apps ────────────────────────
# These bypass .exe path resolution entirely. Windows handles them natively.
# Use `os.system("start <scheme>")` to launch — works even after app updates.
URI_SCHEMES = {
	"whatsapp":   "whatsapp://",
	"spotify":    "spotify://",
	"calculator": "calculator://",
	"settings":   "ms-settings:",
	"store":      "ms-windows-store://",
	"mail":       "mailto:",
	"calendar":   "outlookcal:",
	"maps":       "bingmaps:",
	"photos":     "ms-photos:",
	"xbox":       "xbox://",
	"teams":      "msteams:",
}

# ── Hardcoded fast-lookup paths ───────────────────────────────────────────────
APP_PATHS = {
	"whatsapp":      f"{LOCALAPPDATA}\\WhatsApp\\WhatsApp.exe",
	"telegram":      f"{APPDATA}\\Telegram Desktop\\Telegram.exe",
	"discord":       f"{LOCALAPPDATA}\\Discord\\Update.exe --processStart Discord.exe",
	"chrome":        "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
	"firefox":       "C:\\Program Files\\Mozilla Firefox\\firefox.exe",
	"edge":          "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
	"spotify":       f"{APPDATA}\\Spotify\\Spotify.exe",
	"vscode":        f"{LOCALAPPDATA}\\Programs\\Microsoft VS Code\\Code.exe",
	"vs code":       f"{LOCALAPPDATA}\\Programs\\Microsoft VS Code\\Code.exe",
	"visual studio code": f"{LOCALAPPDATA}\\Programs\\Microsoft VS Code\\Code.exe",
	"notepad":       "notepad.exe",
	"calculator":    "calc.exe",
	"calc":          "calc.exe",
	"paint":         "mspaint.exe",
	"mspaint":       "mspaint.exe",
	"wordpad":       "wordpad.exe",
	"cmd":           "cmd.exe",
	"command prompt":"cmd.exe",
	"powershell":    "powershell.exe",
	"terminal":      "wt.exe",          # Windows Terminal
	"windows terminal": "wt.exe",
	"task manager":  "taskmgr.exe",
	"taskmgr":       "taskmgr.exe",
	"settings":      "ms-settings:",
	"explorer":      "explorer.exe",
	"file explorer": "explorer.exe",
	"excel":         "excel.exe",
	"word":          "winword.exe",
	"powerpoint":    "powerpnt.exe",
	"outlook":       "outlook.exe",
	"onenote":       "onenote.exe",
	"teams":         "msteams.exe",
	"snipping":      "SnippingTool.exe",
	"snipping tool": "SnippingTool.exe",
	"control":       "control.exe",
	"control panel": "control.exe",
	"device manager":"devmgmt.msc",
	"disk management":"diskmgmt.msc",
	"event viewer":  "eventvwr.msc",
	"services":      "services.msc",
	"registry":      "regedit.exe",
	"regedit":       "regedit.exe",
	"notepad++":     "notepad++.exe",
	"vlc":           "vlc.exe",
	"obs":           "obs64.exe",
	"steam":         f"{LOCALAPPDATA}\\Steam\\steam.exe",
	"epic games":    f"{LOCALAPPDATA}\\EpicGamesLauncher\\Portal\\Binaries\\Win64\\EpicGamesLauncher.exe",
}

WINDOW_TITLES = {
	"whatsapp":  ["WhatsApp"],
	"telegram":  ["Telegram"],
	"discord":   ["Discord"],
	"chrome":    ["Google Chrome", "Chrome"],
	"firefox":   ["Mozilla Firefox", "Firefox"],
	"edge":      ["Microsoft Edge"],
	"spotify":   ["Spotify"],
	"vscode":    ["Visual Studio Code", "Code"],
	"vs code":   ["Visual Studio Code", "Code"],
	"notepad":   ["Notepad"],
	"explorer":  ["File Explorer", "This PC"],
	"file explorer": ["File Explorer", "This PC"],
	"excel":     ["Excel", "Microsoft Excel"],
	"word":      ["Word", "Microsoft Word"],
	"powerpoint":["PowerPoint", "Microsoft PowerPoint"],
	"teams":     ["Microsoft Teams"],
	"terminal":  ["Windows Terminal"],
}

# ── Dynamic discovery helpers ─────────────────────────────────────────────────

def _find_in_start_menu(name: str) -> str | None:
	"""Search Windows Start Menu shortcuts for an app by partial name."""
	search_dirs = [
		os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"),
		r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs",
	]
	for base in search_dirs:
		if not os.path.isdir(base):
			continue
		matches = glob.glob(f"{base}/**/*{name}*.lnk", recursive=True)
		if matches:
			return matches[0]
	return None


def _find_in_program_files(name: str) -> str | None:
	"""Search standard Program Files directories for <name>.exe."""
	search_roots = [
		r"C:\Program Files",
		r"C:\Program Files (x86)",
		os.path.expandvars(r"%LOCALAPPDATA%\Programs"),
		os.path.expandvars(r"%APPDATA%"),
	]
	for root in search_roots:
		if not os.path.isdir(root):
			continue
		try:
			matches = glob.glob(f"{root}/**/{name}.exe", recursive=True)
			if matches:
				return matches[0]
		except Exception:
			continue
	return None



class AppLauncher:
	def open(self, app: str, target: str = None) -> str:
		app_key = app.lower().strip()

		# 1. Try to focus an already-open window
		if self._focus_window(app_key):
			return f"Brought {app} to front."

		# 2. URI Scheme — instant launch for Store/modern apps (bypass .exe paths)
		uri = URI_SCHEMES.get(app_key)
		if uri:
			os.system(f"start {uri}")
			time.sleep(0.3)
			return f"Opened {app}."

		# 3. Open folder directly (File Explorer with specific path)
		if app_key in ("explorer", "file explorer") and target:
			folder = self._resolve_folder(target)
			subprocess.Popen(f'explorer.exe "{folder}"')
			return f"Opened {target} folder."

		# 4. Open a file directly if target is a valid file path
		if target and os.path.exists(os.path.expanduser(target)):
			os.startfile(os.path.expanduser(target))
			return f"Opened {target}."

		# 4. Look up in the fast-path dict (known apps)
		#    Normalise key: strip spaces, underscores
		lookup_key = app_key.replace("_", " ")
		path = APP_PATHS.get(lookup_key) or APP_PATHS.get(app_key)

		if path:
			try:
				subprocess.Popen(path, shell=True)
				time.sleep(1.0)
				return f"Opened {app}."
			except Exception as e:
				pass  # fall through to dynamic discovery

		# 5. Dynamic discovery — Windows 'start' command (most universal)
		try:
			subprocess.Popen(
				f'start "" "{app_key}"',
				shell=True,
				stdout=subprocess.DEVNULL,
				stderr=subprocess.DEVNULL,
			)
			time.sleep(1.0)
			return f"Opened {app}."
		except Exception as e:
		    import logging
		    logging.getLogger(__name__).debug(f"Non-critical error: {e}")

		# 6. Dynamic discovery — Start Menu shortcut search
		shortcut = _find_in_start_menu(app_key)
		if shortcut:
			try:
				os.startfile(shortcut)
				time.sleep(1.0)
				return f"Opened {app} (via Start Menu shortcut)."
			except Exception as e:
			    import logging
			    logging.getLogger(__name__).debug(f"Non-critical error: {e}")

		# 7. Dynamic discovery — Program Files .exe search
		exe_path = _find_in_program_files(app_key)
		if exe_path:
			try:
				subprocess.Popen([exe_path])
				time.sleep(1.0)
				return f"Opened {app} (found in Program Files)."
			except Exception as e:
			    import logging
			    logging.getLogger(__name__).debug(f"Non-critical error: {e}")

		return f"Could not find or open '{app}'. Make sure it is installed."

	def open_and_wait(self, app_name: str, timeout: float = 6.0) -> bool:
		"""
		Launch app and poll until its window appears (Fix 1 — WhatsApp cold-start).
		Returns True when window is found, False if it times out.
		"""
		self.open(app_name)

		# Map app name → expected window title keywords
		window_keywords = {
			"whatsapp":  ["WhatsApp"],
			"telegram":  ["Telegram"],
			"discord":   ["Discord"],
			"chrome":    ["Google Chrome", "Chrome"],
			"firefox":   ["Mozilla Firefox", "Firefox"],
			"edge":      ["Microsoft Edge"],
			"spotify":   ["Spotify"],
			"vscode":    ["Visual Studio Code", "Code"],
			"vs code":   ["Visual Studio Code", "Code"],
		}
		titles = window_keywords.get(app_name.lower(), [app_name])
		deadline = time.time() + timeout

		while time.time() < deadline:
			for title in titles:
				wins = gw.getWindowsWithTitle(title)
				if wins:
					try:
						wins[0].activate()
					except Exception as e:
					    import logging
					    logging.getLogger(__name__).debug(f"Non-critical error: {e}")
					time.sleep(0.3)   # let window finish painting
					return True
			time.sleep(0.15)   # poll every 150 ms

		return False   # timed out

	def open_browser_and_navigate(self, url: str, browser: str = "chrome") -> str:
		"""
		Open a browser with a URL already loaded as a CLI argument (Fix 4).
		Avoids a separate navigation step — browser opens directly on the target page.
		"""
		import subprocess

		if not url.startswith("http"):
			url = "https://" + url

		browser_exe = {
			"chrome":  r"C:\Program Files\Google\Chrome\Application\chrome.exe",
			"firefox": r"C:\Program Files\Mozilla Firefox\firefox.exe",
			"edge":    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
		}
		exe = browser_exe.get(browser.lower())

		if exe and os.path.exists(exe):
			subprocess.Popen([exe, url])
			return f"Opening {url} in {browser}."

		# Fallback: Ctrl+L after waiting for browser window
		opened = self.open_and_wait(browser, timeout=5.0)
		if opened:
			if PYAUTOGUI_AVAILABLE:
				_pyautogui.hotkey("ctrl", "l")
				time.sleep(0.2)
				import pyperclip
				pyperclip.copy(url)
				_pyautogui.hotkey("ctrl", "v")
				_pyautogui.press("enter")
				return f"Navigated {browser} to {url}."

		return f"Could not open {browser} to navigate to {url}."


	def close(self, app: str) -> str:
		app_key = app.lower()
		proc_map = {
			"whatsapp": "WhatsApp.exe", "telegram": "Telegram.exe",
			"chrome":   "chrome.exe",   "firefox": "firefox.exe",
			"edge":     "msedge.exe",   "spotify": "Spotify.exe",
			"discord":  "Discord.exe",  "vscode":  "Code.exe",
			"notepad":  "notepad.exe",  "excel":   "EXCEL.EXE",
			"word":     "WINWORD.EXE",
		}
		proc_name = proc_map.get(app_key, f"{app}.exe")
		killed = 0
		for proc in psutil.process_iter(['name']):
			if proc.info['name'].lower() == proc_name.lower():
				proc.kill()
				killed += 1
		return f"Closed {app} ({killed} processes)." if killed else f"{app} was not running."

	def _focus_window(self, app_key: str) -> bool:
		titles = WINDOW_TITLES.get(app_key, [app_key])
		for title_keyword in titles:
			matches = [w for w in gw.getAllWindows()
					   if title_keyword.lower() in w.title.lower() and w.visible]
			if matches:
				try:
					win = matches[0]
					if win.isMinimized:
						win.restore()
					win.activate()
					return True
				except Exception as e:
				    import logging
				    logging.getLogger(__name__).debug(f"Non-critical error: {e}")
		return False

	def _resolve_folder(self, name: str) -> str:
		folder_map = {
			"downloads": os.path.expanduser("~/Downloads"),
			"documents": os.path.expanduser("~/Documents"),
			"desktop":   os.path.expanduser("~/Desktop"),
			"pictures":  os.path.expanduser("~/Pictures"),
			"music":     os.path.expanduser("~/Music"),
			"videos":    os.path.expanduser("~/Videos"),
			"appdata":   APPDATA,
			"temp":      os.environ.get("TEMP", "C:\\Temp"),
		}
		return folder_map.get(name.lower(), name)
# AppLauncher executor from system control guide