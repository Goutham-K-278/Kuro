import os
import re

def patch_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # 1. Fix the top level imports
    # Remove 'import pyautogui', 'import pygetwindow as gw', 'import psutil, pyautogui'
    content = re.sub(r'import\s+psutil,\s*pyautogui', 'import psutil\nimport logging\nlogger = logging.getLogger(__name__)\n\ntry:\n    import pyautogui as _pyautogui\n    PYAUTOGUI_AVAILABLE = True\nexcept ImportError:\n    _pyautogui = None\n    PYAUTOGUI_AVAILABLE = False\n', content)
    content = re.sub(r'import\s+pyautogui\n', 'import logging\nlogger = logging.getLogger(__name__)\n\ntry:\n    import pyautogui as _pyautogui\n    PYAUTOGUI_AVAILABLE = True\nexcept ImportError:\n    _pyautogui = None\n    PYAUTOGUI_AVAILABLE = False\n\n', content)
    content = re.sub(r'import\s+pygetwindow\s+as\s+gw', 'try:\n    import pygetwindow as gw\n    PYGETWINDOW_AVAILABLE = True\nexcept ImportError:\n    gw = None\n    PYGETWINDOW_AVAILABLE = False', content)

    # 2. Add guards to functions
    # For pyautogui
    pyautogui_funcs = [
        'def mute', 'def volume_up', 'def volume_down', 'def screenshot',
        'def type_text', 'def press_key', 'def _stream_type_from_llm',
        'def navigate', 'def scroll_in_app', 'def switch_tab', 'def open_whatsapp_chat',
        'def send_whatsapp_message', 'def send_discord_message', 'def play_spotify',
        'def open_browser_and_navigate', 'def click'
    ]
    for func in pyautogui_funcs:
        # Match the function signature up to the colon, preserving it
        pattern = r'(' + re.escape(func) + r'\b[^:]*:\n)(\s+)'
        replacement = r'\1\2if not PYAUTOGUI_AVAILABLE:\n\2    logger.warning("pyautogui not available")\n\2    return "Error: pyautogui unavailable"\n\2'
        content = re.sub(pattern, replacement, content)

    # For pygetwindow
    pygetwindow_funcs = [
        'def _refocus_target_window', 'def _focus_window', 'def open_and_wait',
        'def _find_open_messaging_app', 'def _focus_app_window', 'def _ensure_whatsapp_open',
        'def _verify_highlighted_contact'
    ]
    for func in pygetwindow_funcs:
        pattern = r'(' + re.escape(func) + r'\b[^:]*:\n(?:(\s+)""".*?"""\n)?(\s+))'
        # we have to inject correctly, handling the docstring if present
        def repl(m):
            indent = m.group(3) if m.group(3) else m.group(2)
            if not indent: indent = "    "
            return m.group(1) + f'if not PYGETWINDOW_AVAILABLE: return False\n{indent}'
        content = re.sub(pattern, repl, content, flags=re.DOTALL)

    # 3. Replace all 'pyautogui.' with '_pyautogui.'
    content = content.replace('pyautogui.', '_pyautogui.')

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

files = [
    r'c:\Users\gouth\OneDrive\Documents\Kuro-LLM\agent\executors\system_agent.py',
    r'c:\Users\gouth\OneDrive\Documents\Kuro-LLM\agent\executors\app_launcher.py',
    r'c:\Users\gouth\OneDrive\Documents\Kuro-LLM\agent\executors\navigation_agent.py'
]

for file in files:
    if os.path.exists(file):
        patch_file(file)
        print(f"Patched {file}")
