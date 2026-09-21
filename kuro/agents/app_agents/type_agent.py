"""
kuro/agents/app_agents/type_agent.py
─────────────────────────────────────────────────────────────────
TypeInAppAgent — open an app (or reuse existing window) and type content

Used for: "write a C++ hello world in notepad", "type this in VSCode", etc.

Strategy:
  1. Launch app if not running
  2. Wait for window to appear
  3. Click into main edit area
  4. Paste content via clipboard (handles all unicode/code/symbols)
─────────────────────────────────────────────────────────────────
"""

import asyncio
import logging
import subprocess
import time
from typing import Any

logger = logging.getLogger(__name__)

_APP_CMD = {
    "notepad":   "notepad.exe",
    "wordpad":   "wordpad.exe",
    "word":      "winword.exe",
    "vscode":    "code",
    "code":      "code",
    "terminal":  "wt.exe",
    "notepad++": "notepad++.exe",
}

_WINDOW_TITLES = {
    "notepad":   ".*Notepad.*",
    "wordpad":   ".*WordPad.*",
    "word":      ".*Word.*",
    "vscode":    ".*Visual Studio Code.*",
    "code":      ".*Visual Studio Code.*",
    "terminal":  ".*Windows Terminal.*",
    "notepad++": ".*Notepad\\+\\+.*",
}


class TypeInAppAgent:

    async def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        app_name = args.get("app_name", "notepad").strip().lower()
        content  = args.get("content", "").strip()
        prompt   = args.get("prompt", "").strip()

        if prompt:
            logger.info(f"[TypeAgent] Asking Groq Llama-3 for: {prompt}")
            try:
                import os, httpx
                api_key = os.getenv("GROQ_API_KEY", "")
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers={"Authorization": f"Bearer {api_key}"},
                        json={
                            "model": "llama-3.3-70b-versatile",
                            "messages": [{"role": "user", "content": f"Write exactly what is requested, with no markdown formatting or conversational filler. Request: {prompt}"}],
                            "temperature": 0.2
                        }
                    )
                    resp.raise_for_status()
                    content = resp.json()["choices"][0]["message"]["content"]
                    
                    # Strip out markdown code fences and whitespace
                    import re
                    content = re.sub(r"^```[a-z]*\n?", "", content, flags=re.MULTILINE|re.IGNORECASE)
                    content = re.sub(r"\n?```$", "", content, flags=re.MULTILINE)
                    content = content.strip()
                    
            except Exception as e:
                logger.error(f"[TypeAgent] Groq API error: {e}")
                return {"success": False, "error": f"Failed to generate text: {e}"}

        if not content:
            return {"success": False, "error": "No content or prompt provided to type"}

        logger.info(f"[TypeAgent] Typing {len(content)} chars into '{app_name}'")
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None, self._type_sync, app_name, content
            )
            return result
        except Exception as e:
            logger.error(f"[TypeAgent] Error: {e}")
            return {"success": False, "error": str(e)}

    def _type_sync(self, app_name: str, content: str) -> dict[str, Any]:
        import pyperclip
        from pywinauto import Application
        from pywinauto.keyboard import send_keys

        title_re = _WINDOW_TITLES.get(app_name, f".*{app_name}.*")
        cmd      = _APP_CMD.get(app_name, f"{app_name}.exe")

        # Try to connect to an existing window first
        app = None
        try:
            app = Application(backend="uia").connect(title_re=title_re, timeout=2)
            logger.debug(f"[TypeAgent] Connected to existing '{app_name}' window")
        except Exception:
            # Launch it
            try:
                subprocess.Popen(cmd, shell=True)
                time.sleep(2.5)  # wait for window to fully appear and render
                app = Application(backend="uia").connect(title_re=title_re, timeout=10)
            except Exception as e:
                return {"success": False, "error": f"Could not open {app_name}: {e}"}

        # Focus the window
        try:
            win = app.window(title_re=title_re)
            win.set_focus()
            time.sleep(0.3)
        except Exception as e:
            return {"success": False, "error": f"Could not focus {app_name}: {e}"}

        # Select-all existing content and replace with new content (Ctrl+A → paste)
        send_keys("^a")
        time.sleep(0.1)

        # Paste via clipboard — handles all chars, code, symbols
        pyperclip.copy(content)
        send_keys("^v")
        time.sleep(0.2)

        logger.info(f"[TypeAgent] Typed content into '{app_name}'")
        return {
            "success": True,
            "message": f"Typed content into {app_name}",
            "response_text": f"Done! I've typed the content into {app_name}.",
        }
