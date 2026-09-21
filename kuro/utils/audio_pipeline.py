import asyncio
import collections
import os
import threading
import time
from pathlib import Path
from typing import AsyncGenerator, Callable, Optional

import numpy as np
import sounddevice as sd
from dotenv import load_dotenv

load_dotenv()

import logging
logger = logging.getLogger(__name__)

from kuro.utils.tts import _TTS_PLAYING_FLAG


# ── Constants ─────────────────────────────────────────────────────────────────
SAMPLE_RATE      = 16000
FRAME_LENGTH     = 1280    # OpenWakeWord natively processes 1280 samples (80ms)
RING_BUFFER_SECS = int(os.getenv("RING_BUFFER_SECONDS", 3))
PRE_ROLL_MS      = 200     # ms of audio to prepend before wake word end
VAD_SILENCE_MS   = int(os.getenv("VAD_SILENCE_THRESHOLD_MS", 1800))
MAX_UTTERANCE_MS = 15_000   # hard cap on one command (15 s)

_PROJECT_ROOT    = Path(__file__).parent.parent.parent


class CircularRingBuffer:
    """Fixed-size circular buffer of PCM audio frames."""
    def __init__(self, capacity_frames: int):
        self._buf: collections.deque = collections.deque(maxlen=capacity_frames)
        self._lock = threading.Lock()

    def push(self, frame: np.ndarray) -> None:
        with self._lock:
            self._buf.append(frame.copy())

    def get_last_n_frames(self, n: int) -> np.ndarray:
        with self._lock:
            frames = list(self._buf)[-n:]
        if not frames:
            return np.array([], dtype=np.int16)
        return np.concatenate(frames).astype(np.int16)


class KuroWakeWordListener:
    """
    Continuous background listener using OpenWakeWord and sounddevice.
    Highly optimized for CPU: ~30ms latency, <2% usage.
    """

    def __init__(self, on_wake: Optional[Callable[[], None]] = None):
        self._on_wake = on_wake
        self._oww_model  = None
        self._stream     = None
        self._running    = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._audio_queue: asyncio.Queue = asyncio.Queue()
        self._wake_event: Optional[asyncio.Event] = None
        
        self._frame_length      = FRAME_LENGTH
        capacity = max(1, int(RING_BUFFER_SECS * SAMPLE_RATE / FRAME_LENGTH))
        self._ring = CircularRingBuffer(capacity)
        
        self._silence_threshold = max(1, int(VAD_SILENCE_MS / (FRAME_LENGTH / SAMPLE_RATE * 1000)))
        self._max_frames        = max(1, int(MAX_UTTERANCE_MS / (FRAME_LENGTH / SAMPLE_RATE * 1000)))
        
        self._direct_armed      = False
        self._last_wake_time    = 0.0
        self._echo_blanking_until = 0.0
        
        self._da_consecutive    = 0
        self._da_energy_hist    = collections.deque(maxlen=32)
        
        self._voice_energy_threshold   = 300.0
        self._silence_energy_threshold = 270.0
        self._ambient_med = 120.0
        self._ambient_p90 = 180.0

        # State for active command capture
        self._in_command    = False
        self._silence_count = 0
        self._frame_count   = 0
        self._speech_peak   = 0.0
        self._speech_seen   = False

    def _resolve_model(self) -> str:
        """Locates the .onnx wake word model. Falls back to bundled hey_jarvis ONNX."""
        env_path = os.getenv("WAKEWORD_MODEL_PATH", "")
        if env_path:
            p = Path(env_path)
            if not p.is_absolute():
                p = _PROJECT_ROOT / p
            if p.exists():
                return str(p)
        
        # Use the bundled ONNX model (full path — bare name fails silently)
        import openwakeword
        oww_dir = Path(openwakeword.__file__).parent / "resources" / "models"
        jarvis_onnx = oww_dir / "hey_jarvis_v0.1.onnx"
        if jarvis_onnx.exists():
            return str(jarvis_onnx)
        raise FileNotFoundError("No wake word model found. Set WAKEWORD_MODEL_PATH in .env")

    def _resolve_verifier(self):
        """Optionally load a custom sklearn verifier for 'Kuro' personalization."""
        env_path = os.getenv("WAKEWORD_VERIFIER_PATH", "")
        if not env_path:
            return None
        p = Path(env_path)
        if not p.is_absolute():
            p = _PROJECT_ROOT / p
        if not p.exists():
            logger.warning(f"[WakeWord] Verifier not found: {p}")
            return None
        try:
            import joblib
            verifier = joblib.load(str(p))
            logger.info(f"[WakeWord] Custom Kuro verifier loaded: {p.name}")
            return verifier
        except Exception as e:
            logger.warning(f"[WakeWord] Could not load verifier: {e}")
            return None

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Initialize OpenWakeWord and start sounddevice stream."""
        from openwakeword.model import Model
        
        self._loop       = loop
        self._wake_event = asyncio.Event()

        model_path = self._resolve_model()
        logger.info(f"[WakeWord] Loading OpenWakeWord model: {model_path}")
        
        # Initialize OWW Model (auto-selects onnx or tflite)
        self._oww_model = Model(wakeword_models=[model_path])
        self._model_name = list(self._oww_model.models.keys())[0]
        
        # Optionally load custom Kuro verifier
        self._verifier = self._resolve_verifier()
        self._verifier_embeddings = []  # rolling window of embeddings for verifier

        # ── Start Audio Stream ──────────────────────────────────────────────
        self._running = True
        
        # Run calibration first (blocking briefly)
        self._calibrate_ambient_fast()

        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype='int16',
            blocksize=FRAME_LENGTH,
            callback=self._audio_callback
        )
        self._stream.start()
        verifier_str = " + Kuro verifier" if self._verifier else " (say 'Hey Jarvis' — train kuro: python scripts/record_kuro.py)"
        logger.info(f"[WakeWord] Listener ready — model: {self._model_name}{verifier_str}")

    def _calibrate_ambient_fast(self):
        """Quickly calibrate background noise using sounddevice."""
        logger.info("[WakeWord] Calibrating ambient noise (1s)...")
        duration = 1.0
        try:
            recording = sd.rec(int(duration * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype='int16')
            sd.wait()
            # Split into frames of 1280
            frames = np.array_split(recording.flatten(), len(recording) // FRAME_LENGTH)
            samples = [float(np.sqrt(np.mean(f.astype(np.float32)**2))) for f in frames if len(f) > 0]
            
            if samples:
                self._ambient_p90 = float(np.percentile(samples, 90))
                self._ambient_med = float(np.median(samples))
                self._voice_energy_threshold = max(150.0, self._ambient_p90 * 1.2)
                self._silence_energy_threshold = min((self._ambient_p90 + self._voice_energy_threshold) / 2, self._voice_energy_threshold * 0.95)
                logger.info(f"[WakeWord] Calibrated — Ambient p90={self._ambient_p90:.0f}, voice_thresh={self._voice_energy_threshold:.0f}")
        except Exception as e:
            logger.warning(f"[WakeWord] Calibration failed: {e}")

    def stop(self) -> None:
        """Stop capture and release resources."""
        self._running = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
        logger.info("[WakeWord] Stopped")

    def _audio_callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        """
        Called automatically by sounddevice for every audio frame (C-level thread).
        This executes the wake word inference and VAD routing.
        """
        if not self._running:
            return
            
        if status:
            logger.debug(f"[WakeWord] Stream status: {status}")

        frame = indata[:, 0].copy()  # Mono int16
        
        if not self._in_command:
            self._ring.push(frame)
            
            # 1. Echo Shield: Feed silence to model while TTS is playing
            if _TTS_PLAYING_FLAG.is_set():
                frame = np.zeros_like(frame)
            if self._direct_armed:
                energy_da = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))
                _now = time.time()
                self._da_energy_hist.append(energy_da)
                
                if _now >= self._echo_blanking_until:
                    _baseline = float(np.median(self._da_energy_hist)) if len(self._da_energy_hist) >= 8 else self._ambient_p90
                    _VOICE_FLOOR = max(120.0, _baseline * 1.18, _baseline + 80.0)
                    
                    if energy_da > _VOICE_FLOOR:
                        self._da_consecutive += 1
                    else:
                        self._da_consecutive = 0
                        
                    if self._da_consecutive >= 2 and (_now - self._last_wake_time) >= 1.0:
                        logger.info(f"[WakeWord] Direct-arm triggered — energy={energy_da:.0f}")
                        self._trigger_wake()
                        return

            # 4. Check OpenWakeWord Prediction
            prediction = self._oww_model.predict(frame)
            score = prediction.get(self._model_name, 0.0)
            
            # Lower threshold: OWW base models need 0.3+ for non-native accents
            OWW_THRESHOLD = float(os.getenv("WAKEWORD_THRESHOLD", "0.3"))
            
            _now = time.time()
            if (_now - self._last_wake_time) > 2.0:
                is_wake = False
                
                # If we have a custom verifier, run it directly on the raw audio features
                if self._verifier is not None:
                    # Require actual audio energy before running verifier to prevent false positives on silence
                    energy = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))
                    if energy > max(self._ambient_med * 1.2, 50.0):
                        try:
                            # Extract the raw 96-dim embedding for this frame
                            feat = self._oww_model.preprocessor.get_features(
                                self._oww_model.model_inputs[self._model_name]
                            ).flatten()
                            
                            # Use verifier probability to avoid false positives.
                            prob = self._verifier.predict_proba([feat])[0][1]
                            
                            # Set threshold to 0.85 for the custom verifier
                            if prob > 0.85:
                                is_wake = True
                                score = prob
                        except Exception as e:
                            logger.error(f"[WakeWord] Verifier error: {e}")
                else:
                    # Fallback to standard OWW model
                    if score > OWW_THRESHOLD:
                        is_wake = True
                
                if is_wake:
                    logger.info(f"[WakeWord] 'Kuro' detected! (score={score:.2f}, custom_verifier={self._verifier is not None})")
                    self._trigger_wake()

        else:
            # 3. We are actively recording a command (VAD phase)
            if self._loop:
                asyncio.run_coroutine_threadsafe(self._audio_queue.put(frame), self._loop)
            self._frame_count += 1
            
            energy = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))
            if energy > self._speech_peak:
                self._speech_peak = energy
                
            if energy > max(self._ambient_p90 * 1.05, 150.0):
                self._speech_seen = True
                
            # Fail fast if no speech
            if not self._speech_seen and self._frame_count >= 30: # ~2.4 seconds
                self._end_utterance("aborted (no speech)")
                return
                
            # Endpointing logic
            endpoint_thresh = max(80.0, min(self._silence_energy_threshold, self._ambient_p90 + 25.0))
            _active_thresh = endpoint_thresh * 0.75 if self._speech_seen else endpoint_thresh
            
            if energy < _active_thresh:
                self._silence_count += 1
                
            if self._silence_count >= self._silence_threshold or self._frame_count >= self._max_frames:
                self._end_utterance("ended")

    def _trigger_wake(self):
        """Transition state to active command capture."""
        self._direct_armed = False
        self._last_wake_time = time.time()
        self._in_command = True
        self._silence_count = 0
        self._frame_count = 0
        self._speech_peak = 0.0
        self._speech_seen = False
        
        # FIX: Reset OWW model state to prevent stale activations from re-triggering immediately
        if self._oww_model:
            self._oww_model.reset()

        # BUG #3 FIX: Do NOT inject pre-roll from ring buffer.
        # The ring buffer contains the wake word audio itself ("Kuro").
        # Injecting it caused Groq to transcribe the wake word as a command.
        # Instead, we blank the audio queue and wait for fresh speech.
        while True:
            try:
                self._audio_queue.get_nowait()
            except Exception:
                break

        if self._wake_event and self._loop:
            self._loop.call_soon_threadsafe(self._wake_event.set)
        if self._on_wake:
            if self._loop:
                self._loop.call_soon_threadsafe(self._on_wake)

    def _end_utterance(self, reason: str):
        """Transition state back to idle."""
        if self._loop:
            asyncio.run_coroutine_threadsafe(self._audio_queue.put(None), self._loop)
        self._in_command = False
        logger.info(f"[WakeWord] Utterance {reason}: {self._frame_count} frames.")

    # ── Public API ────────────────────────────────────────────────────────────

    def drain_and_arm(self) -> None:
        """Arm for speech-triggered capture (post-greeting)."""
        if self._wake_event:
            self._wake_event.clear()
        while True:
            try:
                self._audio_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self._da_consecutive = 0
        self._da_energy_hist.clear()
        self._echo_blanking_until = time.time() + 0.80
        self._direct_armed = True
        logger.debug("[WakeWord] Armed for direct speech capture (echo blanking 0.80s)")

    async def wait_for_command(self) -> AsyncGenerator[np.ndarray, None]:
        if self._wake_event is None:
            raise RuntimeError("Start must be called first")
        self._wake_event.clear()
        await self._wake_event.wait()
        return self.stream_command_audio()

    async def stream_command_audio(self) -> AsyncGenerator[np.ndarray, None]:
        while True:
            chunk = await self._audio_queue.get()
            if chunk is None:
                return
            yield chunk
