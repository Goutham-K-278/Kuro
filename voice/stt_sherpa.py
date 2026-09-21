"""
KURO - Speech-to-Text (STT) via Sherpa-ONNX
Blazing fast CPU-bound streaming speech recognition.
"""
import logging
import os
import threading
import time
from pathlib import Path

from core.config import (
    MIC_SAMPLE_RATE,
    STT_INITIAL_TIMEOUT_S,
    STT_MAX_RECORD_SECONDS,
    STT_MIN_RMS,
)

logger = logging.getLogger(__name__)

_MIC_LOCK = threading.Lock()
_listen_active = False
_listen_active_lock = threading.Lock()

class SherpaSTT:
    def __init__(self):
        self.sample_rate = MIC_SAMPLE_RATE
        self.recognizer = None
        self._load_model()

    def _load_model(self):
        try:
            import sherpa_onnx
            import urllib.request
            import tarfile

            model_dir = os.path.join(os.path.dirname(__file__), "models", "sherpa_onnx")
            model_name = "sherpa-onnx-streaming-zipformer-en-2023-02-21"
            model_path = os.path.join(model_dir, model_name)

            if not os.path.exists(model_path):
                logger.info("Downloading Sherpa-ONNX model (this may take a minute)...")
                os.makedirs(model_dir, exist_ok=True)
                url = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{model_name}.tar.bz2"
                tar_path = os.path.join(model_dir, f"{model_name}.tar.bz2")
                urllib.request.urlretrieve(url, tar_path)
                with tarfile.open(tar_path, "r:bz2") as tar:
                    tar.extractall(path=model_dir)
                os.remove(tar_path)
                logger.info("Sherpa-ONNX Model downloaded and extracted.")

            self.recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
                tokens=os.path.join(model_path, "tokens.txt"),
                encoder=os.path.join(model_path, "encoder-epoch-99-avg-1.onnx"),
                decoder=os.path.join(model_path, "decoder-epoch-99-avg-1.onnx"),
                joiner=os.path.join(model_path, "joiner-epoch-99-avg-1.onnx"),
                num_threads=min(4, os.cpu_count() or 1),
                sample_rate=16000,
                feature_dim=80,
                enable_endpoint_detection=True,
                rule1_min_trailing_silence=2.4,
                rule2_min_trailing_silence=1.2,   # Faster endpoint detection
                rule3_min_utterance_length=20,
                decoding_method="modified_beam_search",  # More accurate than greedy
                max_active_paths=8,                       # Beam width for accuracy
                provider="cpu",
            )
            logger.info("Sherpa-ONNX streaming model loaded — beam_search(8) enabled.")

        except ImportError:
            logger.error("sherpa-onnx not installed! Run: pip install sherpa-onnx")
            raise

    def listen(self, timeout: float = None) -> str:
        """
        Record from mic and transcribe in real-time until silence.
        """
        global _listen_active

        # Fast non-blocking check
        with _listen_active_lock:
            if _listen_active:
                logger.warning("listen() already in progress — dropping duplicate call")
                return ""
            _listen_active = True

        try:
            import sounddevice as sd
            import numpy as np
            import queue

            q = queue.Queue()
            def cb(indata, frames, time_info, status):
                q.put(indata.copy())

            if not _MIC_LOCK.acquire(timeout=0.5):
                logger.warning("Mic busy — dropping this listen request")
                return ""

            try:
                stream = self.recognizer.create_stream()
                t0 = time.perf_counter()
                
                started_at = time.monotonic()
                speech_detected = False
                transcript = ""
                initial_timeout = timeout if timeout is not None else STT_INITIAL_TIMEOUT_S

                # Track energy to discard background noise
                all_audio = []

                with sd.InputStream(
                    samplerate=16000, 
                    channels=1, 
                    dtype="float32", 
                    callback=cb
                ):
                    while True:
                        now = time.monotonic()
                        elapsed = now - started_at

                        if elapsed >= STT_MAX_RECORD_SECONDS:
                            break

                        try:
                            # 10ms chunk block so we don't spin CPU
                            chunk = q.get(timeout=0.01)
                        except queue.Empty:
                            if not speech_detected and elapsed >= initial_timeout:
                                break
                            continue

                        chunk_f32 = chunk[:, 0]
                        all_audio.append(chunk_f32)
                        
                        stream.accept_waveform(16000, chunk_f32)

                        while self.recognizer.is_ready(stream):
                            self.recognizer.decode(stream)

                        result = self.recognizer.get_result(stream)
                        if result and result.strip():
                            if not speech_detected:
                                speech_detected = True
                        
                        # Use Sherpa's endpoint feature to detect end of speech
                        if self.recognizer.is_endpoint(stream) and speech_detected:
                            break

                t1 = time.perf_counter()

                # Merge audio to check RMS noise gate
                if all_audio:
                    full_audio = np.concatenate(all_audio)
                    # Convert float32 [-1, 1] back to int16 [32768, 32767] scale to check against STT_MIN_RMS
                    rms = int(np.sqrt(np.mean((full_audio * 32768.0) ** 2)))
                    if rms < STT_MIN_RMS:
                        logger.info(f"⛔ Sherpa Noise gate: RMS={rms} < {STT_MIN_RMS}. Discarding chatter.")
                        return ""
                
                transcript = self.recognizer.get_result(stream).strip()
                if not transcript:
                    logger.info(f"⏱ Record [Sherpa]: {t1-t0:.2f}s (no speech)")
                    return ""

                # Apply some common corrections since we bypassed whisper's post-processing
                from voice.stt import _STT_CORRECTIONS
                for pattern, replacement in _STT_CORRECTIONS:
                    transcript = pattern.sub(replacement, transcript)

                transcript = transcript.strip()
                logger.info(f"⏱ Transcribe [Sherpa]: {t1-t0:.2f}s → '{transcript[:60]}'")
                return transcript

            finally:
                _MIC_LOCK.release()

        except Exception as e:
            logger.error(f"Sherpa STT failed: {e}")
            return ""
        finally:
            with _listen_active_lock:
                _listen_active = False
