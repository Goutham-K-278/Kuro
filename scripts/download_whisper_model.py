"""Download faster-whisper large-v3-turbo model. Run this directly in a terminal."""
import os
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

print("=" * 60)
print("  Downloading faster-whisper large-v3-turbo (~1.5 GB)")
print("  This will take ~10-15 minutes. DO NOT close this window.")
print("=" * 60)

import huggingface_hub
path = huggingface_hub.snapshot_download("mobiuslabsgmbh/faster-whisper-large-v3-turbo")

print("\nDone! Model saved to:", path)
for f in os.listdir(path):
    sz = os.path.getsize(os.path.join(path, f))
    print(f"  {f}: {sz/1024/1024:.1f} MB")

# Quick verify
from faster_whisper import WhisperModel
print("\nLoading model to verify...")
m = WhisperModel("large-v3-turbo", device="cpu", compute_type="int8")
print("Model loaded successfully!")
