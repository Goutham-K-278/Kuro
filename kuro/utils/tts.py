"""
kuro/utils/tts.py
─────────────────────────────────────────────────────────────────
Stage 4: Streaming TTS with Kokoro-ONNX

• 100% offline — no internet required
• Sentence-streaming: first word plays in ~100ms
• Splits LLM output at sentence boundaries and synthesises in parallel

Model files (auto-downloaded by setup_models.py):
  models/kokoro-v1.0.onnx
  models/voices-v1.0.bin
─────────────────────────────────────────────────────────────────
"""

import asyncio
import logging
import queue as thread_queue
import re
import threading
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# ── Audio Loop Feedback Shield ────────────────────────────────────────────────
# Import _TTS_PLAYING from voice.tts so the STT VAD loop discards mic frames
# while Kuro is speaking. Prevents echo feedback.
try:
    from voice.tts import _TTS_PLAYING as _TTS_PLAYING_FLAG
except ImportError:
    import threading as _thr
    _TTS_PLAYING_FLAG = _thr.Event()  # dummy — always clear

_PROJECT_ROOT = Path(__file__).parent.parent.parent
_MODELS_DIR   = _PROJECT_ROOT / "models"
MODEL_PATH    = _MODELS_DIR / "kokoro-v1.0.onnx"
VOICES_PATH   = _MODELS_DIR / "voices-v1.0.bin"
VOICE_ID      = "af_nova"   # change to "am_adam" for male
SPEED         = 1.1
SAMPLE_RATE   = 24000

# Greetings pre-rendered into RAM at startup so they play instantly (<50ms)
GREETINGS = [
    "Yeah?", "I'm here.", "What's up?",
    "Online.", "Listening.", "Go ahead.", "Ready.",
    "Hey, I'm online. What do you need?",
]

_CACHE_DIR = _PROJECT_ROOT / "data" / "tts_cache"


class StreamingTTS:
    """
    Kokoro-ONNX TTS with sentence-streaming for minimal first-word latency.

    Usage:
        tts = StreamingTTS()
        await tts.speak("On it...")                  # fires in ~100ms
        await tts.speak_stream("Longer response...") # sentence-by-sentence
        tts.interrupt()                              # cut off speech
        tts.shutdown()                               # clean exit
    """

    def __init__(self):
        if not MODEL_PATH.exists():
            raise FileNotFoundError(
                f"Kokoro model missing: {MODEL_PATH}\n"
                "Run:  python setup_models.py"
            )
        # Note: We do NOT instantiate Kokoro here. It is fetched lazily via model_manager
        # in _synthesize() to prevent OOM race conditions at startup.
        self._play_queue = thread_queue.Queue()
        self._greeting_cache: dict[str, np.ndarray] = {}  # phrase → pre-rendered audio
        self._thread = threading.Thread(
            target=self._playback_loop, daemon=True, name="kuro-tts"
        )
        self._thread.start()
        logger.info(f"[TTS] StreamingTTS (Kokoro-ONNX) ready - voice: {VOICE_ID}")

    # ── Prewarm ────────────────────────────────────────────────────────────

    async def prewarm(self) -> None:
        """
        Pre-render all GREETINGS phrases into RAM.
        Call once at startup — after this, speak_greeting() plays in <50ms.
        """
        import hashlib
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        loop = asyncio.get_event_loop()
        logger.info("[TTS] Pre-warming greetings...")
        for phrase in GREETINGS:
            key  = hashlib.md5(phrase.encode()).hexdigest()
            path = _CACHE_DIR / f"{key}.npy"
            if path.exists():
                audio = await loop.run_in_executor(None, np.load, str(path))
            else:
                audio = await loop.run_in_executor(None, self._synthesize, phrase)
                await loop.run_in_executor(None, np.save, str(path), audio)
            self._greeting_cache[phrase] = audio
        logger.info(f"[TTS] Pre-warmed {len(GREETINGS)} greetings ✅")

    async def speak_greeting(self) -> None:
        """Instant greeting — plays a random pre-cached audio without any synthesis."""
        import random
        phrase = random.choice(GREETINGS)
        audio  = self._greeting_cache.get(phrase)
        if audio is not None:
            done = threading.Event()
            self._play_queue.put((audio, done))
            await asyncio.get_event_loop().run_in_executor(None, done.wait)
        else:
            # Cache not ready — fall back to normal synthesis
            await self.speak(phrase)

    # ── Public API ─────────────────────────────────────────────────────────

    async def speak(self, text: str) -> None:
        """Synthesise one phrase and play it — waits until done."""
        if not text.strip():
            return
        done = threading.Event()
        samples = await asyncio.get_event_loop().run_in_executor(
            None, self._synthesize, text
        )
        self._play_queue.put((samples, done))
        await asyncio.get_event_loop().run_in_executor(None, done.wait)

    async def speak_stream(self, text: str) -> None:
        """Split at sentence boundaries, synthesise + play each in order sequentially."""
        sentences = _split_sentences(text)
        if not sentences:
            return
        loop = asyncio.get_event_loop()
        
        for s in sentences:
            # Synthesize sequentially to avoid ONNX thread-pool deadlocks
            samples = await loop.run_in_executor(None, self._synthesize, s)
            done = threading.Event()
            self._play_queue.put((samples, done))
            
            # Wait for playback to finish before synthesizing the next sentence
            await loop.run_in_executor(None, done.wait)

    def interrupt(self) -> None:
        """Immediately stop speech and drain the queue."""
        try:
            import sounddevice as sd
            sd.stop()
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug(f"Non-critical error: {e}")
        while True:
            try:
                self._play_queue.get_nowait()
            except thread_queue.Empty:
                break
        logger.debug("[TTS] Interrupted")

    def shutdown(self) -> None:
        self._play_queue.put(None)
        self._thread.join(timeout=2)

    # ── Internal ───────────────────────────────────────────────────────────

    def _synthesize(self, text: str) -> np.ndarray:
        from core.model_manager import model_manager
        kokoro_model = model_manager.get_sync("kokoro")
        samples, _sr = kokoro_model.create(
            text, voice=VOICE_ID, speed=SPEED, lang="en-us"
        )
        return samples

    def _playback_loop(self) -> None:
        import sounddevice as sd
        while True:
            item = self._play_queue.get()
            if item is None:
                break
            samples, done_event = item
            _TTS_PLAYING_FLAG.set()  # ← Shield ON: STT discards mic frames
            try:
                sd.play(samples, samplerate=SAMPLE_RATE)
                import time as _t
                # Manually sleep for the duration of the audio to avoid PortAudio WASAPI deadlocks
                duration = len(samples) / SAMPLE_RATE
                _t.sleep(duration)
            except Exception as e:
                logger.error(f"[TTS] Playback error: {e}")
            finally:
                import time as _t
                _t.sleep(0.05)  # 50ms tail — reverb guard
                _TTS_PLAYING_FLAG.clear()  # ← Shield OFF: STT resumes
                done_event.set()


def _split_sentences(text: str) -> list[str]:
    raw = re.split(r"(?<=[.!?])\s+", text.strip())
    return [s.strip() for s in raw if len(s.strip()) >= 4]

