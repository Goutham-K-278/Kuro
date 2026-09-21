"""
KURO - Speech-to-Text (STT)
Uses Distil-Whisper: 50% faster + 50% less VRAM than standard Whisper
Model: distil-small.en (~400MB VRAM)
"""
import logging
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Final

from core.config import (
    MIC_SAMPLE_RATE,
    STT_DEVICE,
    STT_INITIAL_TIMEOUT_S,
    STT_MAX_RECORD_SECONDS,
    STT_PRE_SPEECH_MS,
    STT_SILENCE_MS,
    STT_USE_NOISE_REDUCE,
    STT_VAD_THRESHOLD,
    WHISPER_BEAM_SIZE,
    WHISPER_LANGUAGE,
    WHISPER_MODEL,
)

# Import noise gate threshold (safe fallback if config doesn't have it yet)
try:
    from core.config import STT_MIN_RMS
except ImportError:
    STT_MIN_RMS = 300

logger = logging.getLogger(__name__)

# ── Audio Loop Feedback Shield ────────────────────────────────────────────────
# Import the shared event from tts.py. When TTS is playing (event is set),
# the VAD loop discards mic frames instead of processing them.
# Falls back gracefully if tts.py import fails (standalone STT usage).
try:
    from voice.tts import _TTS_PLAYING as _TTS_PLAYING_FLAG
except ImportError:
    import threading as _thr
    _TTS_PLAYING_FLAG = _thr.Event()  # dummy — always clear

# ── MIC SINGLETON LOCK ────────────────────────────────────────────────────────
# THE CORE FIX: Only one InputStream may be open at any time.
# Without this, duplicate wake words + alert mode stack up 5-10 concurrent
# mic streams → mic saturates → all return empty → Python brain crashes.
_MIC_LOCK = threading.Lock()
_listen_active = False          # fast flag — checked before even acquiring lock
_listen_active_lock = threading.Lock()

# ─── Indian-English / Tanglish initial prompt ────────────────────────────────
# Whisper uses this to "prime" its language model. By listing correct spellings
# of app names, command verbs, and common Tamil-English filler words, it learns
# what vocabulary to expect and dramatically reduces mishearings.
_INITIAL_PROMPT = (
    "Kuro voice assistant commands. "
    # ── App commands ──────────────────────────────────────────────────────
    "Open WhatsApp, send message to contact, open Chrome, open YouTube, "
    "open VS Code, open Discord, open Telegram, open Spotify, open Netflix. "
    # ── Spotify / music commands ──────────────────────────────────────────
    "Play Starboy by The Weeknd on Spotify. "
    "Play Blinding Lights, play Shape of You, play Sunflower on Spotify. "
    "Play next song, pause, resume, stop music, skip song. "
    "Hey Kuro play Starboy in Spotify. Hey Kuro pause the music. "
    "Play a song by Arijit Singh, play Tamil songs, play AR Rahman. "
    # ── General browsing ──────────────────────────────────────────────────
    "Search Google for Python tutorial. Open YouTube and play latest MrBeast video. "
    "Go to gmail dot com, open Amazon, search for laptop on Amazon. "
    # ── Messaging ─────────────────────────────────────────────────────────
    "Send hi to Keerth on WhatsApp. Send message to mom saying I am coming home. "
    # ── System ────────────────────────────────────────────────────────────
    "Set timer for five minutes. Take a screenshot. Open file manager. "
    # ── Language hint ─────────────────────────────────────────────────────
    "Indian English, Tamil accent, natural conversational speech."
)

# ─── Tamil / Indian-English post-processing corrections ──────────────────────
# Ordered list of (regex_pattern, replacement) — applied after Whisper outputs.
# Fixes common STT mishearings for Tamil speakers and ensures app names / command
# words are spelled the way the intent parser expects them.
import re as _re

_STT_CORRECTIONS: list[tuple] = [
    # ── Wake-word prefix strip ────────────────────────────────────────────
    # Remove "hey kuro" / "kuro" at the start so the intent parser only
    # sees the command.
    (_re.compile(r"^(?:hey\s+)?(?:kuro|hero|kyro|k[uo]r[oa])\s*[,.]?\s*",
                 _re.I), ""),

    # ── App names ──────────────────────────────────────────────────────────
    (_re.compile(r"\bwhat'?s\s*app\b",          _re.I), "WhatsApp"),
    (_re.compile(r"\bwhat\s+sapp\b",            _re.I), "WhatsApp"),
    (_re.compile(r"\byou\s*tube\b",             _re.I), "YouTube"),
    (_re.compile(r"\bvs?\.?\s*code\b",          _re.I), "VS Code"),
    (_re.compile(r"\bchrome\s*book\b",          _re.I), "Chrome"),
    (_re.compile(r"\bspot\s*if\s*y\b",          _re.I), "Spotify"),
    (_re.compile(r"\btelgram\b",                _re.I), "Telegram"),
    (_re.compile(r"\btelagram\b",               _re.I), "Telegram"),
    (_re.compile(r"\bdis\s*cord\b",             _re.I), "Discord"),

    # ── Spotify / music command mishearings ───────────────────────────────
    # "play star boy"  →  keep as-is, but fix split variants
    (_re.compile(r"\bstar\s+boy\b",             _re.I), "Starboy"),
    (_re.compile(r"\bblind(?:ing)?\s+lights?\b",_re.I), "Blinding Lights"),
    (_re.compile(r"\bweeknd\b",                 _re.I), "The Weeknd"),
    (_re.compile(r"\bin\s+spot\s*if\s*y\b",     _re.I), "on Spotify"),
    (_re.compile(r"\bspot\s*if\s*y\b",          _re.I), "Spotify"),
    # "play in spotify" / "play on spotify" normalise
    (_re.compile(r"\bplay\s+(.+?)\s+(?:in|on)\s+spotify\b", _re.I),
     lambda m: f"play {m.group(1)} on Spotify"),

    # ── Common Whisper hallucinations for Tamil-accented short commands ────
    # These are words/phrases Whisper generates instead of actual commands.
    # Only strip if they are the ENTIRE transcript (i.e. pure hallucination).
    (_re.compile(r"^(?:thank\s*you\.?|thanks\.?|thank\s*you\s*very\s*much\.?|"
                 r"you\.?|okay\.?|ok\.?|bye\.?|hmm+\.?|uh+\s*huh\.?)$",
                 _re.I), ""),

    # ── Command verbs (common Tamil-accented mishearings) ──────────────────
    (_re.compile(r"\bopen\s+up\b",              _re.I), "open"),
    (_re.compile(r"\bclose\s+down\b",           _re.I), "close"),
    (_re.compile(r"\bplay\s+it\b",              _re.I), "play"),
    (_re.compile(r"\bsend\s+a\s+message\b",     _re.I), "send message"),
    (_re.compile(r"\bsearch\s+(?:for\s+)?it\b", _re.I), "search"),

    # ── Tamil filler words at end of sentence (strip them) ─────────────────
    (_re.compile(r"\s+\b(da|di|na|ma|ra|pa|bro|machan|yaar|anna|akka)\s*$",
                 _re.I), ""),

    # ── Common sentence-final Tamil interjections (mid-sentence too) ───────
    (_re.compile(r"\b(po|po\s+da|apdiye)\b",   _re.I), ""),

    # ── Punctuation cleanup ────────────────────────────────────────────────
    (_re.compile(r"\s{2,}"),                    " "),    # collapse double spaces
    (_re.compile(r"^\s+|\s+$"),                 ""),     # trim
]


class STT:
    """Distil-Whisper based speech-to-text transcription."""

    def __init__(self):
        self.model = None
        self.sample_rate = MIC_SAMPLE_RATE
        self._vad_model = None
        self._load_model()

    def _ensure_windows_cuda_dlls(self) -> None:
        """
        On Windows, CUDA-enabled CTranslate2 may require cuBLAS DLLs to be discoverable.
        If NVIDIA pip runtime packages are installed (e.g. `nvidia-cublas-cu12`),
        add their `bin/` folders to the DLL search path automatically.
        """
        if os.name != "nt" or STT_DEVICE != "cuda":
            return
        try:
            import ctypes
            import sys
            from pathlib import Path as _Path

            site_packages = _Path(sys.prefix) / "Lib" / "site-packages"
            candidates = [
                site_packages / "nvidia" / "cublas" / "bin",
                site_packages / "nvidia" / "cuda_runtime" / "bin",
                site_packages / "nvidia" / "cudnn" / "bin",
            ]
            for d in candidates:
                if d.exists():
                    os.add_dll_directory(str(d))

            # Probe (optional): this will succeed if cuBLAS is now discoverable.
            try:
                ctypes.WinDLL("cublas64_12.dll")
            except Exception as e:
                import logging
                logging.getLogger(__name__).debug(f"Non-critical error: {e}")
        except Exception:
            # If anything goes wrong here, CUDA load will just fall back to CPU below.
            return

    def _load_model(self) -> None:
        """Load Whisper via model_manager — gets VRAM gate + real unload for free."""
        try:
            from core.model_manager import model_manager
            # model_manager.get_sync() must be called from a thread (not event loop).
            # At __init__ time we ARE in a thread (called from STT.__init__ which
            # is called synchronously before the event loop starts), so this is safe.
            self.model = model_manager.get_sync("whisper")
            logger.info(f"Whisper loaded via model_manager (VRAM gate) on {STT_DEVICE}")
            return
        except RuntimeError as e:
            # VRAM too low — model_manager refused to load on GPU
            logger.warning(f"[STT] model_manager rejected GPU load ({e}) — trying CPU direct")
        except Exception as e:
            logger.warning(f"[STT] model_manager fallback ({e}) — loading directly")

        # Direct fallback (CPU) — bypasses gate but keeps Kuro running
        try:
            from faster_whisper import WhisperModel
            self._ensure_windows_cuda_dlls()
            self.model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
            logger.info(f"Distil-Whisper loaded (direct CPU fallback): {WHISPER_MODEL}")
        except ImportError:
            logger.error("faster-whisper not installed. Run: pip install faster-whisper")
            raise

    def _get_vad_model(self):
        """
        Use Silero VAD bundled in `faster-whisper` for real-time mic cutoff.
        This is fast, CPU-friendly, and avoids a fixed recording window.
        """
        if self._vad_model is not None:
            return self._vad_model
        try:
            from faster_whisper.vad import get_vad_model

            self._vad_model = get_vad_model()
            return self._vad_model
        except Exception as e:
            raise RuntimeError(f"Could not load Silero VAD from faster-whisper: {e}")

    def _apply_corrections(self, text: str) -> str:
        """Apply Tamil/Indian-English post-processing corrections to raw Whisper output."""
        for pattern, replacement in _STT_CORRECTIONS:
            text = pattern.sub(replacement, text)
        return text.strip()

    def transcribe_file(self, audio_path: str | Path) -> str:
        """
        Transcribe an audio file.
        Supports: .wav, .mp3, .ogg, .flac
        Returns: Transcribed text string
        """
        if self.model is None:
            raise RuntimeError("STT model not loaded")

        segments, info = self.model.transcribe(
            str(audio_path),
            beam_size=WHISPER_BEAM_SIZE,
            language=WHISPER_LANGUAGE,
            initial_prompt=_INITIAL_PROMPT,
            condition_on_previous_text=False,
            without_timestamps=True,           # skip alignment — faster
        )

        transcript = self._apply_corrections(
            " ".join(seg.text for seg in segments).strip()
        )
        logger.debug(f"Transcribed: '{transcript[:80]}...")
        return transcript

    def transcribe_bytes(self, audio_bytes: bytes, sample_rate: int = 16000) -> str:
        """
        Transcribe raw audio bytes (from microphone).
        audio_bytes: PCM 16-bit mono audio
        """
        import numpy as np

        audio_array = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0

        if STT_USE_NOISE_REDUCE and audio_array.size > 0:
            try:
                import importlib
                nr = importlib.import_module("noisereduce")

                # Use the first 250ms as a noise profile when available
                noise_len = min(int(sample_rate * 0.25), audio_array.size)
                noise_clip = audio_array[:noise_len]
                audio_array = nr.reduce_noise(
                    y=audio_array,
                    y_noise=noise_clip if noise_len >= int(sample_rate * 0.05) else None,
                    sr=sample_rate,
                    stationary=True,
                    prop_decrease=0.8,
                ).astype(np.float32, copy=False)
            except Exception as e:
                logger.debug(f"Noise reduction skipped: {e}")

        segments, _ = self.model.transcribe(
            audio_array,
            beam_size=3,                       # 3 = good accuracy/speed balance (was 1)
            language=WHISPER_LANGUAGE,
            initial_prompt=_INITIAL_PROMPT,
            condition_on_previous_text=False,
            without_timestamps=True,           # skip alignment — 15-30% faster
            vad_filter=False,                  # already VAD-filtered by record_until_silence
            temperature=0.0,                   # greedy at each beam step — no random sampling
        )

        raw = " ".join(seg.text for seg in segments).strip()
        return self._apply_corrections(raw)

    def record_until_silence(self, timeout: float = None) -> tuple[bytes, int]:
        """
        Real-time mic capture using Silero VAD (via faster-whisper).
        - Starts when speech is detected
        - Stops quickly after you go silent
        - timeout: max seconds to wait for speech before returning empty
          (uses STT_INITIAL_TIMEOUT_S if None)
        Returns (pcm16_bytes, sample_rate)
        """
        try:
            import sounddevice as sd
            import numpy as np
            import queue

            RATE: Final[int] = int(self.sample_rate)
            FRAME_SAMPLES: Final[int] = 512  # required by SileroVADModel in faster-whisper (32ms @ 16kHz)
            FRAME_MS: Final[float] = (FRAME_SAMPLES / RATE) * 1000.0

            vad_model = self._get_vad_model()

            pre_frames = max(1, int(STT_PRE_SPEECH_MS / FRAME_MS))
            silence_frames = max(1, int(STT_SILENCE_MS / FRAME_MS))
            trigger_frames = 1  # 32ms to confirm speech — was 2 (64ms)

            audio_q: "queue.Queue[np.ndarray]" = queue.Queue()

            def cb(indata, frames, time_info, status):
                # `indata` shape: (frames, channels)
                audio_q.put(indata.copy())

            logger.info(
                f"Listening (VAD) — threshold={STT_VAD_THRESHOLD}, silence={STT_SILENCE_MS}ms, "
                f"max={STT_MAX_RECORD_SECONDS}s"
            )

            started_at = time.monotonic()
            speech_started = False
            speech_hits = 0
            silent_count = 0
            max_vad_prob = 0.0      # diagnostic
            total_frames = 0        # diagnostic

            pre = deque(maxlen=pre_frames)
            out_chunks: list[np.ndarray] = []

            # ── MUTEX: only one InputStream at a time ──────────────────────────────
            if not _MIC_LOCK.acquire(timeout=0.5):
                logger.warning("Mic busy — dropping this listen request")
                return b"", RATE

            try:
              with sd.InputStream(
                samplerate=RATE,
                channels=1,
                dtype=np.int16,
                blocksize=FRAME_SAMPLES,
                callback=cb,
              ):
                while True:
                    now = time.monotonic()
                    elapsed = now - started_at

                    if elapsed >= STT_MAX_RECORD_SECONDS:
                        break

                    try:
                        chunk = audio_q.get(timeout=0.05)  # 5x snappier than 0.25s
                    except queue.Empty:
                        initial_timeout = timeout if timeout is not None else STT_INITIAL_TIMEOUT_S
                        if not speech_started and elapsed >= initial_timeout:
                            if timeout is None:
                                logger.warning(
                                    f"VAD initial timeout ({initial_timeout}s) — "
                                    f"max_prob={max_vad_prob:.3f}, frames={total_frames}, "
                                    f"threshold={STT_VAD_THRESHOLD}. "
                                    f"{'Mic may not be capturing audio!' if total_frames == 0 else 'Speech too quiet or mic issue.'}"
                                )
                            return b"", RATE
                        continue

                    # ── FEEDBACK SHIELD ───────────────────────────────────────
                    # If Kuro's TTS is currently playing, discard this mic frame
                    # entirely. This drains the queue (preventing backup) but
                    # guarantees Kuro can never hear and re-process its own voice.
                    if _TTS_PLAYING_FLAG.is_set():
                        logger.debug("[STT] TTS active — discarding mic frame (feedback shield)")
                        # Also reset any partial speech state so we don't carry
                        # over phantom speech_hits from before TTS started.
                        speech_hits = 0
                        pre.clear()
                        continue
                    # ─────────────────────────────────────────────────────────

                    chunk_i16 = chunk.reshape(-1).astype(np.int16, copy=False)
                    chunk_f32 = chunk_i16.astype(np.float32) / 32768.0

                    # Silero VAD (0..1 speech probability per frame)
                    p = float(vad_model(chunk_f32)[0])
                    is_speech = p >= STT_VAD_THRESHOLD
                    total_frames += 1
                    if p > max_vad_prob:
                        max_vad_prob = p

                    if not speech_started:
                        pre.append(chunk_i16)
                        if is_speech:
                            speech_hits += 1
                            if speech_hits >= trigger_frames:
                                speech_started = True
                                out_chunks.extend(list(pre))
                                pre.clear()
                        else:
                            speech_hits = max(0, speech_hits - 1)
                        continue

                    out_chunks.append(chunk_i16)

                    if is_speech:
                        silent_count = 0
                    else:
                        silent_count += 1
                        if silent_count >= silence_frames:
                            break

            finally:
                _MIC_LOCK.release()  # ALWAYS release, even on exception

            if not out_chunks:
                return b"", RATE

            audio_all = np.concatenate(out_chunks, axis=0)
            return audio_all.tobytes(), RATE

        except ImportError:
            raise RuntimeError("sounddevice not installed. Run: pip install sounddevice")
        except Exception as e:
            logger.error(f"Recording failed: {e}")
            return b"", int(self.sample_rate)

    def listen(self, timeout: float = None) -> str:
        """
        Listen and transcribe. Drops duplicate calls instantly via _listen_active flag.
        This is the SECOND layer of protection (first is _MIC_LOCK in record_until_silence).
        timeout: max seconds to wait for speech. If None, uses STT_INITIAL_TIMEOUT_S.
        """
        global _listen_active

        # Fast non-blocking check before even trying the lock
        with _listen_active_lock:
            if _listen_active:
                logger.warning("listen() already in progress — dropping duplicate call")
                return ""
            _listen_active = True

        try:
            t0 = time.perf_counter()
            audio_bytes, sr = self.record_until_silence(timeout=timeout)
            t1 = time.perf_counter()
            if not audio_bytes:
                logger.info(f"⏱ Record: {t1-t0:.2f}s (no speech)")
                return ""
            logger.info(f"⏱ Record: {t1-t0:.2f}s ({len(audio_bytes)/2/sr:.1f}s audio)")

            # ── Noise gate: discard background chatter ─────────────────────────
            # Classroom noise arrives at low volume (low RMS) vs. the user
            # speaking directly into the laptop mic (high RMS).
            import numpy as np
            audio_arr = np.frombuffer(audio_bytes, dtype=np.int16)
            rms = int(np.sqrt(np.mean(audio_arr.astype(np.float64) ** 2)))
            if rms < STT_MIN_RMS:
                logger.info(
                    f"⛔ Noise gate: RMS={rms} < {STT_MIN_RMS} — "
                    f"discarding (likely background chatter, not user speech)"
                )
                return ""
            logger.debug(f"Noise gate passed: RMS={rms} >= {STT_MIN_RMS}")

            text = self.transcribe_bytes(audio_bytes, sr)
            t2 = time.perf_counter()
            logger.info(f"⏱ Transcribe: {t2-t1:.2f}s → '{text[:60]}'")
            return text
        finally:
            with _listen_active_lock:
                _listen_active = False  # ALWAYS reset, even on exception

    def listen_streaming(self, on_chunk: callable, chunk_seconds: float = 3.0,
                         max_seconds: float = 120.0, done_silence_ms: int = 1500) -> str:
        """
        Streaming dictation — transcribe in chunks and call on_chunk(text) for each.
        The caller can type each chunk in real-time while the user keeps speaking.

        Args:
            on_chunk:        Called with each transcribed text chunk as it's ready
            chunk_seconds:   Seconds of audio per transcription chunk (~3s = fast feedback)
            max_seconds:     Total max recording time
            done_silence_ms: Silence duration (ms) to end the entire session

        Returns: Full concatenated transcript
        """
        try:
            import sounddevice as sd
            import numpy as np
            import queue

            RATE: Final[int] = int(self.sample_rate)
            FRAME_SAMPLES: Final[int] = 512
            FRAME_MS: Final[float] = (FRAME_SAMPLES / RATE) * 1000.0

            vad_model = self._get_vad_model()

            chunk_frames = int(chunk_seconds * RATE / FRAME_SAMPLES)
            done_silence_frames = max(1, int(done_silence_ms / FRAME_MS))
            trigger_frames = 1

            audio_q: "queue.Queue[np.ndarray]" = queue.Queue()

            def cb(indata, frames, time_info, status):
                audio_q.put(indata.copy())

            logger.info(
                f"Streaming dictation — chunk={chunk_seconds}s, max={max_seconds}s, "
                f"done_silence={done_silence_ms}ms"
            )

            started_at = time.monotonic()
            speech_started = False
            speech_hits = 0
            silent_count = 0
            current_chunk: list[np.ndarray] = []
            chunk_frame_count = 0
            full_transcript_parts: list[str] = []

            pre = deque(maxlen=max(1, int(STT_PRE_SPEECH_MS / FRAME_MS)))

            # ── MUTEX: only one InputStream at a time ──────────────────────────
            if not _MIC_LOCK.acquire(timeout=0.5):
                logger.warning("Mic busy — skipping streaming listen")
                return ""

            try:
              with sd.InputStream(
                samplerate=RATE, channels=1, dtype=np.int16,
                blocksize=FRAME_SAMPLES, callback=cb,
              ):
                while True:
                    elapsed = time.monotonic() - started_at
                    if elapsed >= max_seconds:
                        break

                    try:
                        frame = audio_q.get(timeout=0.05)
                    except queue.Empty:
                        if not speech_started and elapsed >= STT_INITIAL_TIMEOUT_S:
                            break
                        continue

                    chunk_i16 = frame.reshape(-1).astype(np.int16, copy=False)
                    chunk_f32 = chunk_i16.astype(np.float32) / 32768.0
                    p = float(vad_model(chunk_f32)[0])
                    is_speech = p >= STT_VAD_THRESHOLD

                    # Wait for speech to start
                    if not speech_started:
                        pre.append(chunk_i16)
                        if is_speech:
                            speech_hits += 1
                            if speech_hits >= trigger_frames:
                                speech_started = True
                                current_chunk.extend(list(pre))
                                pre.clear()
                                chunk_frame_count = len(current_chunk)
                        else:
                            speech_hits = max(0, speech_hits - 1)
                        continue

                    current_chunk.append(chunk_i16)
                    chunk_frame_count += 1

                    if is_speech:
                        silent_count = 0
                    else:
                        silent_count += 1

                    # Done speaking entirely (long silence)
                    if silent_count >= done_silence_frames:
                        # Transcribe remaining chunk
                        if current_chunk:
                            text = self._transcribe_chunk(current_chunk, RATE)
                            if text:
                                on_chunk(text)
                                full_transcript_parts.append(text)
                        break

                    # Chunk boundary — transcribe what we have and keep going
                    # Only flush on a short speech pause (>200ms silence) to avoid
                    # cutting mid-word
                    short_pause = silent_count >= max(1, int(200 / FRAME_MS))
                    if chunk_frame_count >= chunk_frames and short_pause:
                        text = self._transcribe_chunk(current_chunk, RATE)
                        if text:
                            on_chunk(text)
                            full_transcript_parts.append(text)
                        current_chunk = []
                        chunk_frame_count = 0
                        silent_count = 0

            finally:
                _MIC_LOCK.release()  # ALWAYS release, even on exception

            # Flush any leftover audio
            if current_chunk:
                text = self._transcribe_chunk(current_chunk, RATE)
                if text:
                    on_chunk(text)
                    full_transcript_parts.append(text)

            full = " ".join(full_transcript_parts)
            logger.info(f"Streaming dictation done: {len(full)} chars, {len(full_transcript_parts)} chunks")
            return full

        except Exception as e:
            logger.error(f"Streaming dictation failed: {e}")
            try:
                _MIC_LOCK.release()
            except RuntimeError:
                pass
            return ""

    def _transcribe_chunk(self, frames: list, sample_rate: int) -> str:
        """Transcribe a list of int16 numpy arrays into text."""
        import numpy as np
        if not frames:
            return ""
        audio = np.concatenate(frames, axis=0)
        audio_bytes = audio.tobytes()
        raw = self.transcribe_bytes(audio_bytes, sample_rate)
        return raw   # corrections already applied inside transcribe_bytes
