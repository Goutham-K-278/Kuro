"""
KURO - System Commander
Handles OS-level tasks: open apps, screenshots, system info, file ops
Runs LOCAL only — no cloud needed for system commands
"""
import logging
import subprocess
from pathlib import Path

import psutil

logger = logging.getLogger(__name__)


class Commander:
    """Execute system commands and gather system information."""

    # App launch registry (expandable by user)
    APP_MAP = {
        "chrome":   "chrome",
        "firefox":  "firefox",
        "notepad":  "notepad",
        "spotify":  "spotify",
        "vscode":   "code",
        "explorer": "explorer",
        "blender":  "blender",
        "terminal": "wt",           # Windows Terminal
        "cmd":      "cmd",
        "youtube":  "https://youtube.com",
    }

    def execute(self, user_input: str) -> dict:
        """
        Parse user command and execute the appropriate action.
        Returns: {message: str, action: str, success: bool}
        """
        cmd = user_input.lower().strip()

        # Screenshot
        if "screenshot" in cmd:
            return self._take_screenshot()

        # Open app
        for app_name in self.APP_MAP:
            if app_name in cmd and ("open" in cmd or "launch" in cmd or "start" in cmd):
                return self._open_app(app_name)

        # Clipboard read
        if "clipboard" in cmd or "copy" in cmd:
            return self._get_clipboard()

        # Volume (stub — implement with pycaw or nircmd)
        if "volume" in cmd:
            return {"message": "Volume control not yet implemented.", "action": "volume", "success": False}

        # Type / write text
        for prefix in ("type ", "write ", "input "):
            if cmd.startswith(prefix):
                text = user_input[len(prefix):].strip()
                return self._type_text(text)

        # Press a key
        if cmd.startswith("press "):
            key = cmd[6:].strip()
            return self._press_key(key)

        # Fallback — hand off to the agent action system
        return {"message": f"Forwarded to action system: {user_input}", "action": "forward", "success": True}

    def _take_screenshot(self) -> dict:
        """Take and save a screenshot."""
        try:
            from PIL import ImageGrab
            from datetime import datetime

            save_dir = Path.home() / "Pictures" / "Kuro_Screenshots"
            save_dir.mkdir(parents=True, exist_ok=True)

            filename = f"kuro_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            path = save_dir / filename

            img = ImageGrab.grab()
            img.save(path)

            return {
                "message": f"Screenshot saved to: {path}",
                "action": "screenshot",
                "success": True,
                "path": str(path),
            }
        except Exception as e:
            return {"message": f"Screenshot failed: {e}", "action": "screenshot", "success": False}

    def _open_app(self, app_name: str) -> dict:
        """Launch an application or URL."""
        target = self.APP_MAP.get(app_name, app_name)

        try:
            if target.startswith("http"):
                import webbrowser
                webbrowser.open(target)
            else:
                subprocess.Popen([target], shell=True)

            return {"message": f"Opened {app_name}.", "action": f"open_{app_name}", "success": True}
        except Exception as e:
            return {"message": f"Could not open {app_name}: {e}", "action": "open_app", "success": False}

    def _type_text(self, text: str) -> dict:
        """Type text into the current active window — uses smart_type for intelligence."""
        try:
            from agent.executors.system_agent import SystemAgent
            result = SystemAgent().smart_type(text)
            return {"message": result, "action": "type", "success": True}
        except Exception as e:
            return {"message": f"Type failed: {e}", "action": "type", "success": False}

    def _press_key(self, key: str) -> dict:
        """Press a single key or hotkey."""
        try:
            import pyautogui
            if '+' in key:
                parts = [k.strip() for k in key.split('+')]
                pyautogui.hotkey(*parts)
            else:
                pyautogui.press(key.strip())
            return {"message": f"Pressed {key}.", "action": "press", "success": True}
        except Exception as e:
            return {"message": f"Press failed: {e}", "action": "press", "success": False}

    def _get_clipboard(self) -> dict:
        """Read clipboard content."""
        try:
            import pyperclip
            content = pyperclip.paste()
            return {
                "message": f"Clipboard: {content[:200]}",
                "action": "clipboard_read",
                "success": True,
            }
        except Exception as e:
            return {"message": f"Clipboard error: {e}", "action": "clipboard", "success": False}

    def get_system_info(self) -> dict:
        """Collect CPU, RAM, disk, and GPU stats."""
        info = {}

        try:
            cpu = psutil.cpu_percent(interval=1)
            cpu_freq = psutil.cpu_freq()
            info["cpu"] = {
                "usage_percent": cpu,
                "freq_mhz": round(cpu_freq.current) if cpu_freq else "N/A",
                "cores": psutil.cpu_count(logical=True),
            }
        except Exception:
            info["cpu"] = {}

        try:
            mem = psutil.virtual_memory()
            info["memory"] = {
                "total_gb": round(mem.total / 1e9, 1),
                "used_gb":  round(mem.used  / 1e9, 1),
                "percent":  mem.percent,
            }
        except Exception:
            info["memory"] = {}

        try:
            disk = psutil.disk_usage("/")
            info["disk"] = {
                "total_gb": round(disk.total / 1e9, 1),
                "used_gb":  round(disk.used  / 1e9, 1),
                "percent":  disk.percent,
            }
        except Exception:
            info["disk"] = {}

        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.used,memory.total,temperature.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                parts = result.stdout.strip().split(", ")
                info["gpu"] = {
                    "name": parts[0] if len(parts) > 0 else "N/A",
                    "vram_used_mb": parts[1] if len(parts) > 1 else "N/A",
                    "vram_total_mb": parts[2] if len(parts) > 2 else "N/A",
                    "temp_c": parts[3] if len(parts) > 3 else "N/A",
                }
        except Exception:
            info["gpu"] = {"name": "NVIDIA (nvidia-smi unavailable)"}

        try:
            battery = psutil.sensors_battery()
            if battery:
                info["battery"] = {
                    "percent": battery.percent,
                    "plugged": battery.power_plugged,
                }
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug(f"Non-critical error: {e}")

        return info
