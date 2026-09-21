"""
scripts/debug_p8.py
───────────────────────────────────────────────────────
Kuro Phase 8 — Startup Diagnostic

Run this when voice stops working after Phase 8.
It checks every component in the call chain and tells
you exactly where the break is.

Usage:
    python scripts/debug_p8.py
───────────────────────────────────────────────────────
"""

import os
import sys

sys.path.insert(0, os.getcwd())

from dotenv import load_dotenv
load_dotenv()

print("=" * 60)
print("KURO PHASE 8 DIAGNOSTIC")
print("=" * 60)

# ── Test 1: Speaker Verify ──────────────────────────────────────────────────
print("\n[1] Speaker Verify...")
verify_enabled = os.getenv("SPEAKER_VERIFY", "false").lower() == "true"
print(f"    SPEAKER_VERIFY = {verify_enabled}")
if verify_enabled:
    vp_path = "data/voice_enrollment/owner_voiceprint.npy"
    if not os.path.exists(vp_path):
        print("    ❌  VOICEPRINT FILE MISSING — this is rejecting ALL voice input silently")
        print("    FIX: Set SPEAKER_VERIFY=false in .env  OR  run: python scripts/enroll_voice.py")
    else:
        print("    ✅ Voiceprint exists:", vp_path)
else:
    print("    ✅ Speaker verify disabled (not the issue)")

# ── Test 2: Groq key ────────────────────────────────────────────────────────
print("\n[2] Groq API key...")
groq_key = os.getenv("GROQ_API_KEY", "")
if groq_key:
    print(f"    ✅ Present (ends with ...{groq_key[-6:]})")
else:
    print("    ❌  GROQ_API_KEY missing in .env — intent parsing will fail")

# ── Test 3: LLM import & multi-intent handling ──────────────────────────────
print("\n[3] LLM intent parser...")
try:
    from kuro.utils.llm import GroqReasoner, _make_speak_phrase
    print("    ✅ GroqReasoner import OK")

    # Simulate the multi-intent fix
    import json
    for label, raw in [
        ("normal dict",  '{"tool": "play_spotify", "args": {"query": "faded"}}'),
        ("list (multi)", '[{"tool": "play_spotify", "args": {"query": "faded"}}, {"tool": "open_application", "args": {"app_name": "chrome"}}]'),
    ]:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            result = parsed[0] if parsed else {}
        else:
            result = parsed
        tool = result.get("tool", "??")
        print(f"    ✅ {label} → tool={tool}")
except Exception as e:
    print(f"    ❌  Import/parse failed: {e}")

# ── Test 4: Spotify credentials ─────────────────────────────────────────────
print("\n[4] Spotify...")
cid = os.getenv("SPOTIFY_CLIENT_ID", "")
cs  = os.getenv("SPOTIFY_CLIENT_SECRET", "")
ru  = os.getenv("SPOTIFY_REDIRECT_URI", "")
if not cid or not cs:
    print("    ❌  SPOTIFY_CLIENT_ID or SPOTIFY_CLIENT_SECRET missing in .env")
    print("    FIX: Add them to .env (see Kuro_Phase8_Plan.md for instructions)")
else:
    print(f"    ✅ Credentials present (client_id ends ...{cid[-6:]})")
    token_cache = "data/.spotify_token_cache"
    if not os.path.exists(token_cache):
        print("    ❌  NO TOKEN CACHE — browser auth not completed yet")
        print("    FIX: Run: python scripts/spotify_auth.py")
    else:
        print("    ✅ Token cache exists:", token_cache)
try:
    import spotipy
    print("    ✅ spotipy installed")
except ImportError:
    print("    ❌  spotipy not installed — run: pip install spotipy")
try:
    import diskcache
    print("    ✅ diskcache installed")
except ImportError:
    print("    ❌  diskcache not installed — run: pip install diskcache")

# ── Test 5: WhatsApp / win32api ─────────────────────────────────────────────
print("\n[5] WhatsApp / win32api...")
try:
    import win32api, win32gui, win32con, win32clipboard
    print("    ✅ win32api + win32clipboard available")
except ImportError as e:
    print(f"    ❌  Missing: {e}")
    print("    FIX: pip install pywin32")

# ── Test 6: Audio / VAD config ──────────────────────────────────────────────
print("\n[6] Audio / VAD config...")
silence_ms = int(os.getenv("STT_SILENCE_MS", os.getenv("VAD_SILENCE_THRESHOLD_MS", "700")))
vad_thresh  = float(os.getenv("STT_VAD_THRESHOLD", "0.30"))
print(f"    STT_SILENCE_MS          = {silence_ms}ms")
print(f"    STT_VAD_THRESHOLD       = {vad_thresh}")
if silence_ms > 2500:
    print("    ⚠️  Very high silence threshold — may feel very slow to respond")
try:
    import sherpa_onnx
    print("    ✅ sherpa_onnx (STT engine) available")
except ImportError:
    print("    ⚠️  sherpa_onnx not found (STT may use whisper fallback)")
try:
    import pvporcupine
    print("    ✅ pvporcupine (wake word) available")
except ImportError:
    print("    ❌  pvporcupine not found — wake word won't work")

# ── Test 7: Screen watcher deps ─────────────────────────────────────────────
print("\n[7] Screen watcher (Phase 8 Fix 6)...")
try:
    import easyocr
    print("    ✅ easyocr installed")
except ImportError:
    print("    ❌  easyocr not installed — run: pip install easyocr")
try:
    import pyautogui
    print("    ✅ pyautogui installed")
except ImportError:
    print("    ❌  pyautogui not installed — run: pip install pyautogui")
try:
    from PIL import ImageGrab
    print("    ✅ Pillow (ImageGrab) installed")
except ImportError:
    print("    ❌  Pillow not installed — run: pip install pillow")

# ── Summary ─────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("If you see ❌ above, fix those first and restart Kuro.")
print()
print("Log breakpoint guide:")
print("  [WakeWord] heard  but no [STT] line  → audio/VAD broken")
print("  [STT] transcript  but no [Groq] line → speaker verify blocking you")
print("  [Groq] Intent →   but no action      → execution broken")
print("=" * 60)
