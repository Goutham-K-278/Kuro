"""
KURO - Text-to-Speech (TTS)
Uses edge-tts for high-quality, natural-sounding voice synthesis.
Supports personalized greetings and response speaking.
"""
import asyncio
import hashlib
import io
import logging
import os
import random
import tempfile
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# ─── Audio Loop Feedback Shield ───────────────────────────────────────────────
# _TTS_PLAYING is set() while Kuro is speaking and clear() when silent.
# The STT VAD loop imports this and discards mic frames while it is set,
# making it physically impossible for Kuro's own voice to re-enter the pipeline.
_TTS_PLAYING = threading.Event()   # exported — imported by voice/stt.py

# TTS backend selection from config
try:
    from core.config import TTS_BACKEND as _TTS_BACKEND_CFG
except ImportError:
    _TTS_BACKEND_CFG = "edge"
TTS_BACKEND_ACTIVE = _TTS_BACKEND_CFG  # "kokoro" | "edge" | "pyttsx3"

# TTS cache directory (pre-generated for common phrases)
CACHE_DIR = Path("data/tts_cache")

# Phrases to pre-generate at startup for instant (~50ms) playback
COMMON_PHRASES = [
    # Greetings
    "Yeah?", "What is it?", "Hmm?", "I'm listening.", "Yes?", "I'm here.",
    # Confirmations
    "Done.", "Got it.", "On it.", "Sure.", "Alright.", "Okay.",
    # Action results
    "Opened.", "Closed.", "Saved.", "Copied.", "Deleted.",
    "Timer set.", "Screenshot taken.",
    # Errors
    "I couldn't do that.", "Something went wrong.", "Try again.",
]

# Personalized greetings — played immediately when wake word is detected
GREETINGS = [
    "Yeah, how can I help you?",
    "I'm listening.",
    "Yes, what do you need?",
    "I'm here. What's up?",
    "Go ahead, I'm listening.",
    "What can I do for you?",
]

# Edge TTS voice — natural sounding
VOICE = "en-US-GuyNeural"  # Male, natural voice


class TTS:
    """Text-to-Speech engine using edge-tts."""

    def __init__(self):
        self._playing = False
        self._stop_flag = False
        self._cache: dict = {}   # {md5_hash: file_path} for instant cached playback
        self._kokoro = None      # lazy-loaded Kokoro TTS engine
        self._kokoro_failed = False  # True if Kokoro init failed (skip retries)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        # Pre-warm cache for common phrases in background
        threading.Thread(target=self._pre_cache_sync, daemon=True).start()
        # Pre-load Kokoro if configured
        if TTS_BACKEND_ACTIVE == "kokoro":
            threading.Thread(target=self._init_kokoro, daemon=True).start()

    def greet(self) -> None:
        """Play a random greeting (non-blocking)."""
        greeting = random.choice(GREETINGS)
        logger.info(f"Greeting: '{greeting}'")
        self._speak_async(greeting)

    def ack(self) -> None:
        """Play a short acknowledgment tone (~150ms) — instant, no network."""
        def _play():
            try:
                import numpy as np
                import sounddevice as sd
                sr = 16000
                t = np.linspace(0, 0.15, int(sr * 0.15), False, dtype=np.float32)
                tone = (np.sin(2 * np.pi * 600 * t) * 0.3).astype(np.float32)
                fade = int(0.01 * sr)
                tone[:fade] *= np.linspace(0, 1, fade, dtype=np.float32)
                tone[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)
                sd.play(tone, sr)
                sd.wait()
            except Exception as e:
                import logging
                logging.getLogger(__name__).debug(f"Non-critical error: {e}")
        threading.Thread(target=_play, daemon=True).start()

    def speak_welcome(self, name: str = "Goutham") -> None:
        """Startup welcome greeting — speaks once when Kuro boots."""
        msg = f"Welcome back, {name}. Kuro is ready."
        logger.info(f"Welcome: '{msg}'")
        self._speak_async(msg)

    def speak(self, text: str) -> None:
        """Speak text aloud (non-blocking)."""
        if not text or not text.strip():
            return
        logger.info(f"Speaking: '{text[:60]}...'")
        self._speak_async(text)

    def speak_sync(self, text: str) -> None:
        """Speak text and wait until finished."""
        if not text or not text.strip():
            return
        logger.info(f"Speaking (sync): '{text[:60]}...'")
        self._speak_blocking(text)

    def speak_and_play(self, text: str) -> None:
        """Backward-compatible alias used by older call sites."""
        self.speak_sync(text)

    def stop(self) -> None:
        """Stop any ongoing speech."""
        self._stop_flag = True
        self._playing = False

    def speak_action_result(self, result: str) -> None:
        """
        Speak an action result — optimized for brevity.
        Action confirmations ≤ 8 words go as-is; longer results get
        truncated to the first sentence only.
        """
        if not result:
            self.speak("Done.")
            return
        if len(result.split()) <= 8:
            self.speak(result)
        else:
            first = result.split(".")[0].strip()
            self.speak((first + ".") if first else result[:100])

    def _random_greeting(self) -> str:
        """Return a random greeting string."""
        return random.choice(GREETINGS)

    def _pre_cache_sync(self) -> None:
        """Synchronously pre-generate cached TTS files for COMMON_PHRASES."""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._pre_cache_async())
        except Exception as e:
            logger.debug(f"TTS pre-cache error: {e}")
        finally:
            loop.close()

    async def _pre_cache_async(self) -> None:
        """Generate and cache all COMMON_PHRASES via edge-tts."""
        try:
            import edge_tts
        except ImportError:
            return
        for phrase in COMMON_PHRASES:
            cache_key = hashlib.md5(phrase.encode()).hexdigest()
            cache_path = CACHE_DIR / f"{cache_key}.mp3"
            if cache_path.exists():
                self._cache[cache_key] = str(cache_path)
                continue
            try:
                communicate = edge_tts.Communicate(phrase, VOICE)
                await asyncio.wait_for(communicate.save(str(cache_path)), timeout=8.0)
                self._cache[cache_key] = str(cache_path)
                logger.debug(f"TTS cached: '{phrase}'")
            except Exception as e:
                import logging
                logging.getLogger(__name__).debug(f"Non-critical error: {e}")

    @property
    def is_playing(self) -> bool:
        return self._playing

    def _speak_async(self, text: str) -> None:
        """Speak in a background thread. Routes through Kokoro when configured."""
        self._playing = True  # Set BEFORE thread starts to avoid race with is_playing check
        target_fn = (
            self._speak_blocking_with_kokoro
            if TTS_BACKEND_ACTIVE == "kokoro"
            else self._speak_blocking
        )
        thread = threading.Thread(target=target_fn, args=(text,), daemon=True)
        thread.start()

    def _speak_blocking(self, text: str) -> None:
        """
        Generate TTS audio and play it.
        Cache check first (∼50ms) → edge-tts (online) → pyttsx3 (offline fallback)
        """
        self._stop_flag = False
        self._playing = True

        # ── Cache hit: instant playback (∼50ms)  ──────────────────────
        cache_key = hashlib.md5(text.encode()).hexdigest()
        if cache_key in self._cache:
            cached_path = self._cache[cache_key]
            if os.path.exists(cached_path):
                try:
                    self._play_audio(cached_path)
                finally:
                    self._playing = False
                return

        temp_path = None
        edge_success = False
        try:
            import edge_tts

            # Generate audio to a temp file — 6 s timeout (was 10 s, reduced for snappier fallback)
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                temp_path = f.name

            async def _generate():
                communicate = edge_tts.Communicate(text, VOICE)
                await asyncio.wait_for(communicate.save(temp_path), timeout=6.0)

            loop = asyncio.new_event_loop()
            loop.run_until_complete(_generate())
            loop.close()

            if self._stop_flag:
                return

            edge_success = True

            # Play the audio
            self._play_audio(temp_path)

        except asyncio.TimeoutError:
            logger.warning("edge-tts: connection timeout — switching to offline TTS")
        except ImportError:
            logger.warning("edge-tts not installed — using offline TTS fallback")
        except Exception as e:
            logger.warning(f"edge-tts failed ({e}) — switching to offline TTS")
        finally:
            self._playing = False
            # Cleanup temp file
            if temp_path:
                try:
                    Path(temp_path).unlink(missing_ok=True)
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).debug(f"Non-critical error: {e}")

        # ── Offline fallback (pyttsx3) ─────────────────────────────────────
        if not edge_success and not self._stop_flag:
            logger.info("TTS fallback: using pyttsx3 (offline)")
            self._playing = True
            try:
                self._speak_offline(text)
            finally:
                self._playing = False

    def _init_kokoro(self) -> None:
        """Load Kokoro via model_manager — gets VRAM gate + real unload for free."""
        if self._kokoro is not None or self._kokoro_failed:
            return
        try:
            from core.model_manager import model_manager
            self._kokoro = model_manager.get_sync("kokoro")
            logger.info("Kokoro TTS loaded via model_manager (VRAM gate)")
        except RuntimeError as e:
            # VRAM too low — model_manager refused to load
            logger.warning(f"[TTS] Kokoro GPU load rejected ({e}) — trying direct CPU")
            self._kokoro_failed = True
        except KeyError:
            # 'kokoro' not registered yet — fall back to direct construction
            logger.info("[TTS] Kokoro not registered in model_manager — loading directly")
            try:
                from kokoro_onnx import Kokoro
                self._kokoro = Kokoro("kokoro-v0_19.onnx", "voices.json")
                logger.info("Kokoro TTS loaded directly (fallback)")
            except ImportError:
                logger.info("kokoro-onnx not installed — Kokoro TTS unavailable")
                self._kokoro_failed = True
            except FileNotFoundError as e:
                logger.info(f"Kokoro model files not found ({e})")
                self._kokoro_failed = True
            except Exception as e:
                logger.warning(f"Kokoro TTS direct init failed: {e}")
                self._kokoro_failed = True
        except Exception as e:
            logger.warning(f"Kokoro TTS init failed: {e}")
            self._kokoro_failed = True

    def _speak_kokoro(self, text: str) -> bool:
        """
        Speak using Kokoro-82M (~100ms per sentence, fully local).
        Returns True if successful, False to fall through to edge-tts.
        """
        if self._kokoro is None:
            self._init_kokoro()
        if self._kokoro is None:
            return False

        _TTS_PLAYING.set()  # ← Shield ON
        try:
            import sounddevice as sd
            import numpy as np

            samples, sample_rate = self._kokoro.create(
                text, voice="af_heart", speed=1.0
            )
            if self._stop_flag:
                return True
            sd.play(samples, sample_rate)
            sd.wait()
            return True
        except Exception as e:
            logger.warning(f"Kokoro TTS playback failed: {e}")
            return False
        finally:
            import time as _t
            _t.sleep(0.25)   # 250ms tail — reverb guard
            _TTS_PLAYING.clear()  # ← Shield OFF

    def _speak_blocking_with_kokoro(self, text: str) -> None:
        """
        Kokoro-first TTS: try Kokoro → edge-tts → pyttsx3.
        Used when TTS_BACKEND is set to 'kokoro'.
        """
        self._stop_flag = False
        self._playing = True

        # Cache hit (always fastest)
        cache_key = hashlib.md5(text.encode()).hexdigest()
        if cache_key in self._cache:
            cached_path = self._cache[cache_key]
            if os.path.exists(cached_path):
                try:
                    self._play_audio(cached_path)
                finally:
                    self._playing = False
                return

        try:
            # Try Kokoro first (~100ms)
            if self._speak_kokoro(text):
                self._playing = False
                return
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug(f"Non-critical error: {e}")

        # Fall through to edge-tts → pyttsx3
        try:
            self._speak_blocking(text)
        finally:
            self._playing = False

    def _speak_offline(self, text: str) -> None:
        """
        pyttsx3 offline TTS — speaks directly to speakers, no network required.
        Used when edge-tts fails (timeout, no internet, service down).
        """
        _TTS_PLAYING.set()  # ← Shield ON
        try:
            import pyttsx3
            engine = pyttsx3.init()
            engine.setProperty("rate", 175)
            engine.setProperty("volume", 0.9)
            # Prefer a natural-sounding male voice (David/Mark on Windows)
            voices = engine.getProperty("voices")
            for v in voices:
                if any(name in v.name.lower() for name in ("david", "mark", "guy", "zira")):
                    engine.setProperty("voice", v.id)
                    break
            engine.say(text)
            engine.runAndWait()
            engine.stop()
        except Exception as e:
            logger.error(f"pyttsx3 offline TTS also failed: {e}")
        finally:
            import time as _t
            _t.sleep(0.25)  # 250ms tail — reverb guard
            _TTS_PLAYING.clear()  # ← Shield OFF

    def _play_audio(self, file_path: str) -> None:
        """Play an audio file using sounddevice + soundfile."""
        _TTS_PLAYING.set()   # ← Shield: block STT mic frames for duration of playback
        try:
            import sounddevice as sd
            import soundfile as sf

            data, samplerate = sf.read(file_path)
            if self._stop_flag:
                return
            sd.play(data, samplerate)
            sd.wait()
        except ImportError:
            # Fallback: use system command
            import subprocess
            import sys
            if sys.platform == "win32":
                subprocess.run(
                    ["powershell", "-c", f'(New-Object Media.SoundPlayer "{file_path}").PlaySync()'],
                    capture_output=True,
                )
            else:
                subprocess.run(["aplay", file_path], capture_output=True)
        except Exception as e:
            logger.error(f"Audio playback failed: {e}")
        finally:
            # Add 250ms tail-silence so the last word's reverb doesn't sneak in
            import time as _t
            _t.sleep(0.25)
            _TTS_PLAYING.clear()  # ← Shield OFF: STT resumes processing mic
