import os
os.environ["ONNX_PROVIDER"] = "CUDAExecutionProvider"
import time
from pathlib import Path
from kokoro_onnx import Kokoro

print("Loading Kokoro...")
_root = Path("main.py").parent
_model_path  = str(_root / "models" / "kokoro-v1.0.onnx")
_voices_path = str(_root / "models" / "voices-v1.0.bin")
model = Kokoro(_model_path, _voices_path)
print("Kokoro Loaded. Synthesizing...")
start = time.time()
samples, sr = model.create("Oh man, Kirito is the ultimate hero from Sword Art Online!", voice="af_nova", speed=1.0, lang="en-us")
print(f"Synthesized in {time.time() - start:.2f}s, length: {len(samples)}")

samples, sr = model.create("Oh man, Kirito is the ultimate hero from Sword Art Online!", voice="af_nova", speed=1.0, lang="en-us")
print(f"Synthesized in {time.time() - start:.2f}s, length: {len(samples)}")
