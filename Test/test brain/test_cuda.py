import numpy as np
import time

print("Testing CUDA Whisper...")

from faster_whisper import WhisperModel

model = WhisperModel(
    "large-v3-turbo",
    device="cuda",
    compute_type="int8_float16",
)

audio = np.zeros(16000 * 3, dtype=np.float32)

t = time.time()
segments, info = model.transcribe(audio, beam_size=1, language="en")
list(segments)
elapsed = time.time() - t

print(f"Language: {getattr(info, 'language', 'unknown')}")
print(f"Time: {elapsed:.2f}s")
print("If > 3s -> running on CPU. If < 1.5s -> GPU working.")
