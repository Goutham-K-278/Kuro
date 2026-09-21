import uiautomation as auto
import time

def dump_whatsapp_uia():
    print("Looking for WhatsApp window...")
    wa_window = auto.WindowControl(searchDepth=1, Name="WhatsApp")
    if not wa_window.Exists(3, 1):
        # Could just be that the title is different or it's hiding
        for win in auto.GetRootControl().GetChildren():
            if "WhatsApp" in win.Name:
                wa_window = win
                break
                
    if not wa_window.Exists(0, 0):
        print("WhatsApp not found!")
        return

    print("Found WhatsApp window! Bringing to front...")
    wa_window.SetFocus()
    time.sleep(1)

    print("--- UI Automation Tree Start ---")
    auto.Logger.SetLogFile('wa_uia_tree.txt')
    # Use auto.WalkTree to find the compose box or search box
    for control, depth in auto.WalkControl(wa_window, includeTop=True, maxDepth=6):
        try:
            print(f"{'  ' * depth}{control.ControlType} - Name: '{control.Name}' - AutomationId: '{control.AutomationId}'")
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug(f"Non-critical error: {e}")

    print("--- UI Automation Tree End ---")

if __name__ == '__main__':
    dump_whatsapp_uia()
