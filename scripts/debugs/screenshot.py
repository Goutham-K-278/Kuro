import mss
import mss.tools
with mss.mss() as sct:
    filename = sct.shot(mon=-1, output='wa_screenshot.png')
    print(filename)
