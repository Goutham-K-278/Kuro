#!/usr/bin/env python3
"""
Quick diagnostic to test Kuro components:
1. Groq connectivity
2. Porcupine wake word
3. Kokoro TTS
4. Microphone access
"""

import os
import sys
from pathlib import Path

os.chdir(Path(__file__).parent)
sys.path.insert(0, str(Path(__file__).parent))

print("\n" + "="*60)
print("KURO DIAGNOSTIC — Component Check")
print("="*60 + "\n")

# 1. Check Groq
print("[1/4] Testing Groq API...")
try:
    from kuro.utils.llm import GroqReasoner
    groq = GroqReasoner()
    result = groq.parse_intent("play music on spotify")
    print(f"  ✓ Groq works! Intent: {result.get('tool')}")
except Exception as e:
    print(f"  ✗ Groq failed: {e}")

# 2. Check Porcupine
print("\n[2/4] Testing Porcupine wake word...")
try:
    from kuro.utils.audio_pipeline import KuroWakeWordListener
    import asyncio
    listener = KuroWakeWordListener()
    loop = asyncio.new_event_loop()
    listener.start(loop)
    print(f"  ✓ Porcupine started! Listening for 'Hey Kuro'")
    print(f"    (say 'Hey Kuro' now, or press Ctrl+C to skip)")
    try:
        import time
        for i in range(10):
            time.sleep(1)
            print(f"    waiting... {i+1}s")
    except KeyboardInterrupt:
        print("    (skipped)")
    finally:
        listener.stop()
except Exception as e:
    print(f"  ✗ Porcupine failed: {e}")

# 3. Check Kokoro TTS
print("\n[3/4] Testing Kokoro TTS...")
try:
    import asyncio
    from kuro.utils.tts import StreamingTTS
    tts = StreamingTTS()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(tts.speak("Testing Kokoro voice"))
    print(f"  ✓ Kokoro works! Played test audio")
    tts.shutdown()
except Exception as e:
    print(f"  ✗ Kokoro failed: {e}")

# 4. Check STT
print("\n[4/4] Testing Sherpa-ONNX STT...")
try:
    from kuro.utils.stt import StreamingSTT
    stt = StreamingSTT()
    print(f"  ✓ STT loaded! Ready for streaming")
except Exception as e:
    print(f"  ✗ STT failed: {e}")

print("\n" + "="*60)
print("Diagnostic complete!")
print("="*60 + "\n")
