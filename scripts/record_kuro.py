"""
scripts/record_kuro.py
──────────────────────────────────────────────────────────────────────────────
MANUAL MODE: Press ENTER before each recording.
Step 1: Record your voice saying "Kuro" 20 times.
Step 2: Record 20 silent / background noises.
──────────────────────────────────────────────────────────────────────────────
"""
import os, sys, wave, time
from pathlib import Path

# Force UTF-8 on Windows terminals
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ROOT = Path(__file__).parent.parent

try:
    import sounddevice as sd
    import numpy as np
except ImportError:
    print("ERROR: sounddevice / numpy not installed.")
    sys.exit(1)

SAMPLE_RATE   = 16000
DURATION      = 1.5   
SAMPLES       = int(SAMPLE_RATE * DURATION)
N_POSITIVE    = 20    
N_NEGATIVE    = 20    

POS_DIR = ROOT / "voice" / "training" / "positive"
NEG_DIR = ROOT / "voice" / "training" / "negative"
POS_DIR.mkdir(parents=True, exist_ok=True)
NEG_DIR.mkdir(parents=True, exist_ok=True)


def save_wav(path: Path, audio: np.ndarray):
    audio = audio.flatten().astype(np.int16)
    with wave.open(str(path), 'w') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio.tobytes())


def record_one(label: str) -> np.ndarray:
    print(f"  >>> Recording {DURATION:.1f}s... say '{label}' NOW! ", end="", flush=True)
    audio = sd.rec(SAMPLES, samplerate=SAMPLE_RATE, channels=1, dtype='int16')
    sd.wait()
    rms = float(np.sqrt(np.mean(audio.astype(np.float32)**2)))
    print(f"  [DONE] (RMS={rms:.0f})")
    return audio


# ─── PART 1: Positive samples ("Kuro") ────────────────────────────────────────
print("=" * 60)
print("  PART 1: Record YOUR VOICE saying  K U R O")
print("  Speak clearly and naturally each time.")
print("  Vary your volume, speed, and distance a little each time.")
print("=" * 60)

existing = sorted(POS_DIR.glob("kuro_*.wav"))
start_idx = len(existing)
print(f"  Already have {start_idx} recordings. Recording {N_POSITIVE - start_idx} more.\n")

for i in range(start_idx, N_POSITIVE):
    input(f"  [{i+1:02d}/{N_POSITIVE}] Press ENTER then say 'Kuro'... ")
    audio = record_one("Kuro")
    path = POS_DIR / f"kuro_{i:03d}.wav"
    save_wav(path, audio)
    print(f"       Saved: {path.name}")

print(f"\n  [OK] {N_POSITIVE} positive samples saved to: {POS_DIR}\n")


# ─── PART 2: Negative samples (background / silence / other words) ─────────────
print("=" * 60)
print("  PART 2: Record BACKGROUND NOISE (no wake word)")
print("  Just let the mic capture your room / keyboard / breathing.")
print("  Do NOT say 'Kuro' during these recordings.")
print("=" * 60)

existing_neg = sorted(NEG_DIR.glob("neg_*.wav"))
start_neg = len(existing_neg)
print(f"  Already have {start_neg} recordings. Recording {N_NEGATIVE - start_neg} more.\n")

for i in range(start_neg, N_NEGATIVE):
    input(f"  [{i+1:02d}/{N_NEGATIVE}] Press ENTER — stay QUIET or talk about something else... ")
    audio = record_one("(anything except Kuro)")
    path = NEG_DIR / f"neg_{i:03d}.wav"
    save_wav(path, audio)
    print(f"       Saved: {path.name}")

print(f"\n  [OK] {N_NEGATIVE} negative samples saved to: {NEG_DIR}")
print("\n  Now run:  python scripts/train_kuro.py")
