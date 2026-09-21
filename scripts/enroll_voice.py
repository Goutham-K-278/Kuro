"""
scripts/enroll_voice.py
─────────────────────────────────────────────────────────────────
Enroll your voice once to create a voiceprint for speaker verification.

Run this ONCE before enabling voice security in .env:
    python scripts/enroll_voice.py

After enrollment, set SPEAKER_VERIFY=true in .env to activate.
Kuro will silently reject commands from anyone else's voice.
─────────────────────────────────────────────────────────────────
"""

import os
import sys
import time

import numpy as np
import sounddevice as sd

SAMPLE_RATE   = 16000
RECORD_SECS   = 5
NUM_SAMPLES   = 5
SAMPLES_DIR   = "data/voice_enrollment"
VOICEPRINT_PATH = "data/voice_enrollment/owner_voiceprint.npy"


def record_sample(index: int) -> np.ndarray:
    """Record one 5-second voice sample."""
    print(f"\n  Sample {index + 1}/{NUM_SAMPLES} — speak naturally for {RECORD_SECS}s.")
    print("  Starting in 2 seconds... get ready.")
    time.sleep(2.0)
    print("  🎙  GO — speak now...")
    audio = sd.rec(
        int(RECORD_SECS * SAMPLE_RATE),
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
    )
    sd.wait()
    print("  ✅ Done.")
    return audio.flatten()


def enroll():
    os.makedirs(SAMPLES_DIR, exist_ok=True)

    print("\n" + "═" * 52)
    print("  KURO VOICE ENROLLMENT")
    print("═" * 52)
    print(f"\n  You'll record {NUM_SAMPLES} samples of {RECORD_SECS}s each.")
    print("  Speak naturally — say anything (your name, a sentence, etc.)")
    print("  Stay at your NORMAL speaking distance from the mic.\n")
    print("  Press Enter to start...")
    input()

    try:
        from resemblyzer import VoiceEncoder, preprocess_wav
    except ImportError:
        print("\n❌ resemblyzer not installed. Run:")
        print("   pip install resemblyzer")
        sys.exit(1)

    encoder   = VoiceEncoder(device="cpu")
    embeddings = []

    for i in range(NUM_SAMPLES):
        audio = record_sample(i)

        # Save raw sample for debugging
        np.save(os.path.join(SAMPLES_DIR, f"sample_{i}.npy"), audio)

        # Encode to 256-dim voiceprint embedding
        wav   = preprocess_wav(audio, source_sr=SAMPLE_RATE)
        embed = encoder.embed_utterance(wav)
        embeddings.append(embed)
        print(f"  Embedding {i + 1} computed — shape: {embed.shape}")

    # Average embedding across all samples = stable voiceprint
    voiceprint = np.mean(embeddings, axis=0)
    np.save(VOICEPRINT_PATH, voiceprint)

    print("\n" + "═" * 52)
    print("  ✅ ENROLLMENT COMPLETE")
    print("═" * 52)
    print(f"\n  Voiceprint saved to: {VOICEPRINT_PATH}")
    print("\n  To activate speaker verification:")
    print("  Add to your .env file:")
    print("    SPEAKER_VERIFY=true")
    print("    SPEAKER_VERIFY_THRESHOLD=0.72")
    print("\n  Kuro will now only respond to YOUR voice. 🔒")
    print()


if __name__ == "__main__":
    enroll()
