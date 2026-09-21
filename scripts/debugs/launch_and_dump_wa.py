import sys
import uiautomation as auto
import time
import os
import subprocess
import psutil

def launch_and_dump():
    wa_running = any("whatsapp" in p.name().lower() for p in psutil.process_iter())
    if not wa_running:
        print("Launching WhatsApp...")
        os.system('start whatsapp:')
        time.sleep(5)
    else:
        print("WhatsApp is already running. Forcing to front...")
        os.system('start whatsapp:') # this usually restores it
        time.sleep(2)

    print("Looking for WhatsApp window...")
    # Find all windows and check Title
    wa_win = None
    for win in auto.GetRootControl().GetChildren():
        if win.ControlType == auto.ControlType.WindowControl and "whatsapp" in win.Name.lower():
            wa_win = win
            break
            
    if not wa_win:
        print("Still cannot find WhatsApp window!")
        return

    wa_win.SetFocus()
    time.sleep(1)

    with open('wa_uia_tree.txt', 'w', encoding='utf-8') as f:
        for control, depth in auto.WalkControl(wa_win, includeTop=True, maxDepth=8):
            try:
                line = f"{'  ' * depth}{control.ControlType} - Name: '{control.Name}' - AutomationId: '{control.AutomationId}'"
                f.write(line + "\n")
            except Exception as e:
                import logging
                logging.getLogger(__name__).debug(f"Non-critical error: {e}")
    print("Dumped to wa_uia_tree.txt")

if __name__ == '__main__':
    launch_and_dump()
