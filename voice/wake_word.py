"""
KURO - Wake Word Detection via OpenWakeWord
Ultra-responsive custom wake word engine.
Uses a custom-trained ONNX model for "Kuro".
"""
import logging
import os
import threading
import time
import numpy as np
import pyaudio
import openwakeword
from typing import Callable, Optional
from core.config import OWW_MODEL_PATH, OWW_THRESHOLD

logger = logging.getLogger(__name__)

# Post-detection cooldown — prevents re-triggers from Kuro's own TTS output
_COOLDOWN_SECONDS = 1.5


class WakeWordDetector:
    """
    Always-on background listener for "Kuro" using OpenWakeWord.
    Uses the universal embedding model + custom Kuro head.

    Supports pause()/resume() so other subsystems (STT, TTS, alert-mode)
    can temporarily freeze detection and avoid mic contention.
    """

    def __init__(
        self,
        on_wake: Callable[[], None],
        on_short_wake: Optional[Callable[[], None]] = None,
    ):
        self.on_wake       = on_wake
        self.on_short_wake = on_short_wake or on_wake
        self.running       = False
        self._paused       = False          # True → skip detection frames
        self._thread       = None
        self._stop_event   = threading.Event()
        self._model        = None
        self._audio        = None
        self._stream       = None

    # ── Public control ────────────────────────────────────────────────────

    def start(self) -> None:
        """Start wake word detection in background thread."""
        if self.running:
            logger.warning("Wake word detector already running")
            return
        self._stop_event.clear()
        self._paused = False
        self.running = True
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()
        logger.info(f"OpenWakeWord started — model: {os.path.basename(OWW_MODEL_PATH)}, threshold: {OWW_THRESHOLD}")

    def stop(self) -> None:
        """Stop detection and release audio resources."""
        self.running = False
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        self._cleanup()
        logger.info("Wake word detector stopped")

    def pause(self) -> None:
        """Pause detection (keep stream open — just stop processing frames).
        We do NOT call stop_stream() because PyAudio streams on Windows cannot
        always be reliably restarted, causing permanent OWW failure after
        alert-mode exits.  The _listen_loop already handles _paused gracefully."""
        if not self._paused:
            self._paused = True
            logger.debug("Wake word detection PAUSED")

    def resume(self) -> None:
        """Resume detection.  Resets model state so stale pre-pause audio
        doesn't trigger a false positive on the first frame back."""
        if self._paused:
            self._paused = False
            if self._model:
                try:
                    self._model.reset()
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).debug(f"Non-critical error: {e}")
            logger.debug("Wake word detection RESUMED")

    # ── Internal ──────────────────────────────────────────────────────────

    def _cleanup(self):
        """Release PyAudio and model resources."""
        if self._stream:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception as e:
                import logging
                logging.getLogger(__name__).debug(f"Non-critical error: {e}")
            self._stream = None
        if self._audio:
            try:
                self._audio.terminate()
            except Exception as e:
                import logging
                logging.getLogger(__name__).debug(f"Non-critical error: {e}")
            self._audio = None
        self._model = None

    def _listen_loop(self) -> None:
        """Continuously read audio and check for 'Kuro'."""
        try:
            if not os.path.exists(OWW_MODEL_PATH):
                logger.error(f"Kuro model not found at {OWW_MODEL_PATH} - Run training first!")
                self.running = False
                return

            # Initialize OpenWakeWord with custom model
            self._model = openwakeword.Model(
                wakeword_models=[OWW_MODEL_PATH],
                inference_framework="onnx"
            )

            # OpenWakeWord prefers 1280 samples (80ms) for streaming
            CHUNK_SIZE = 1280

            self._audio = pyaudio.PyAudio()
            self._stream = self._audio.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=16000,
                input=True,
                frames_per_buffer=CHUNK_SIZE
            )

            model_key = os.path.splitext(os.path.basename(OWW_MODEL_PATH))[0]
            logger.info(f"🎤 Listening for 'Kuro' (model_key={model_key}, threshold={OWW_THRESHOLD})...")

            while self.running and not self._stop_event.is_set():
                # While paused, sleep instead of reading the (stopped) stream
                if self._paused:
                    time.sleep(0.1)
                    continue

                # Read audio
                try:
                    data = self._stream.read(CHUNK_SIZE, exception_on_overflow=False)
                except Exception:
                    time.sleep(0.05)
                    continue

                pcm = np.frombuffer(data, dtype=np.int16)
                prediction = self._model.predict(pcm)
                score = prediction.get(model_key, 0)

                if score >= OWW_THRESHOLD:
                    logger.info(f"🎤 WAKE WORD 'Kuro' detected (score={score:.3f})")
                    self.on_wake()
                    # Cooldown — prevents re-triggers from Kuro's own TTS output
                    # Also resets model state so stale audio doesn't re-fire
                    self._stop_event.wait(timeout=_COOLDOWN_SECONDS)
                    if self._model:
                        try:
                            self._model.reset()
                        except Exception as e:
                            import logging
                            logging.getLogger(__name__).debug(f"Non-critical error: {e}")

        except Exception as e:
            logger.error(f"OpenWakeWord loop crashed: {e}")
        finally:
            self._cleanup()
            self.running = False
