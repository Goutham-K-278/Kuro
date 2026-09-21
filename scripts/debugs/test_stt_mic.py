"""
scripts/test_stt_mic.py
-----------------------
Manual STT microphone diagnostic.

Run:  python scripts/test_stt_mic.py

What it does:
  1. Lists all available microphones so you can pick the right one.
  2. Records 5 seconds of audio while printing real-time VAD probabilities.
  3. Shows the RMS of your audio.
  4. Transcribes what you said.
  5. Prints a recommendation if your thresholds need adjusting.

Just speak normally after ">>> Speak now!" appears.
"""

import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Config defaults (mirrors core/config.py) ─────────────────────────────────
try:
    from core.config import (
        STT_VAD_THRESHOLD, STT_MIN_RMS, STT_INITIAL_TIMEOUT_S,
        STT_SILENCE_MS, MIC_SAMPLE_RATE, WHISPER_MODEL,
    )
except Exception:
    STT_VAD_THRESHOLD    = 0.30
    STT_MIN_RMS          = 300
    STT_INITIAL_TIMEOUT_S = 3.0
    STT_SILENCE_MS       = 300
    MIC_SAMPLE_RATE      = 16000
    WHISPER_MODEL        = "distil-small.en"

RATE         = 16000
FRAME        = 512           # 32ms @ 16kHz
RECORD_SECS  = 6             # how long to record


def list_mics():
    import sounddevice as sd
    print("\n=== Available microphones ===")
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            mark = " <-- default" if i == sd.default.device[0] else ""
            print(f"  [{i:2d}] {d['name']}{mark}")
    print()


def record_with_vad(seconds: float):
    """Record audio and print per-frame VAD probabilities in real time."""
    import sounddevice as sd
    import numpy as np
    import queue

    try:
        from faster_whisper.vad import get_vad_model
        vad = get_vad_model()
    except Exception as e:
        print(f"[WARN] Could not load Silero VAD: {e}")
        vad = None

    q = queue.Queue()
    frames_all = []
    max_prob   = 0.0

    def cb(indata, f, t, s):
        q.put(indata.copy())

    n_frames = int(seconds * RATE / FRAME)
    print(f"\n>>> Speak now! Recording {seconds}s  (VAD threshold={STT_VAD_THRESHOLD})\n")

    with sd.InputStream(samplerate=RATE, channels=1, dtype="int16",
                        blocksize=FRAME, callback=cb):
        for i in range(n_frames):
            try:
                chunk = q.get(timeout=1.0)
            except queue.Empty:
                continue

            chunk_i16 = chunk.reshape(-1).astype("int16")
            chunk_f32 = chunk_i16.astype("float32") / 32768.0
            frames_all.append(chunk_i16)

            if vad:
                p = float(vad(chunk_f32)[0])
                if p > max_prob:
                    max_prob = p
                bar = "#" * int(p * 30)
                label = "SPEECH" if p >= STT_VAD_THRESHOLD else "      "
                print(f"  [{i*FRAME/RATE:4.1f}s] {label} prob={p:.3f}  |{bar:<30}|",
                      end="\r")

    print()
    return frames_all, max_prob


def analyse(frames_all, max_prob: float):
    import numpy as np

    if not frames_all:
        print("[ERROR] No audio captured. Check microphone permissions.")
        return

    audio = np.concatenate(frames_all).astype("float32")
    rms   = int(np.sqrt(np.mean((audio * 32768).astype("float64")**2 / 32768**2 * 32768**2).clip(0)))

    print(f"\n=== Audio diagnostics ===")
    print(f"  Duration     : {len(audio)/RATE:.1f}s  ({len(audio)} samples)")
    print(f"  RMS amplitude: {rms}  (noise gate threshold: STT_MIN_RMS={STT_MIN_RMS})")
    print(f"  Peak VAD prob: {max_prob:.3f}  (threshold: STT_VAD_THRESHOLD={STT_VAD_THRESHOLD})")

    # Recommendations
    ok = True
    print("\n=== Recommendations ===")

    if rms < STT_MIN_RMS:
        print(f"  [!] RMS {rms} is BELOW noise gate {STT_MIN_RMS}.")
        print(f"      Your audio will be SILENTLY DISCARDED after transcription.")
        suggested = max(50, rms // 2)
        print(f"      Fix: in .env add  STT_MIN_RMS={suggested}")
        ok = False
    else:
        print(f"  [OK] RMS {rms} passes noise gate ({STT_MIN_RMS}).")

    if max_prob < STT_VAD_THRESHOLD:
        print(f"  [!] Peak VAD prob {max_prob:.3f} never reached threshold {STT_VAD_THRESHOLD}.")
        print(f"      Kuro will NEVER detect your speech as valid — it will time out.")
        suggested_vad = max(0.05, round(max_prob * 0.7, 2))
        print(f"      Fix: in .env add  STT_VAD_THRESHOLD={suggested_vad}")
        ok = False
    elif max_prob < STT_VAD_THRESHOLD + 0.05:
        print(f"  [~] Peak VAD prob {max_prob:.3f} is barely above threshold {STT_VAD_THRESHOLD}.")
        suggested_vad = max(0.05, round(max_prob * 0.6, 2))
        print(f"      Consider lowering: STT_VAD_THRESHOLD={suggested_vad}")
    else:
        print(f"  [OK] VAD prob {max_prob:.3f} well above threshold ({STT_VAD_THRESHOLD}).")

    if ok:
        print("  [OK] All thresholds look fine.")

    return audio


def transcribe(audio_np):
    import numpy as np
    print("\n=== Transcribing... ===")
    try:
        from voice.stt import STT
        stt = STT()
        audio_i16 = (audio_np.clip(-1, 1) * 32768).astype("int16")
        pcm_bytes  = audio_i16.tobytes()
        text = stt.transcribe_bytes(pcm_bytes, RATE)
        print(f"  Result: \"{text}\"")
        if not text.strip():
            print("  [!] Empty transcript — Whisper heard nothing (or only silence/noise).")
    except Exception as e:
        print(f"  [ERROR] {e}")
        import traceback; traceback.print_exc()


if __name__ == "__main__":
    import numpy as np
    print("=" * 60)
    print("   Kuro STT Microphone Diagnostic")
    print("=" * 60)
    list_mics()
    frames, max_prob = record_with_vad(RECORD_SECS)
    audio = analyse(frames, max_prob)
    if audio is not None:
        transcribe(audio)
    print("\nDone. Fix any [!] issues above and re-run to confirm.")
