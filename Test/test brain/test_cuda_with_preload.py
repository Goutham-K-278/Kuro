import time
import numpy as np

import kuro.utils.stt  # triggers CUDA DLL preload
from faster_whisper import WhisperModel

print("Testing CUDA Whisper via STT preload...")
model = WhisperModel("large-v3-turbo", device="cuda", compute_type="int8_float16")
audio = np.zeros(16000 * 3, dtype=np.float32)
t = time.time()
segments, info = model.transcribe(audio, beam_size=1, language="en")
list(segments)
print(f"Language: {getattr(info, 'language', 'unknown')}")
print(f"Time: {time.time() - t:.2f}s")
