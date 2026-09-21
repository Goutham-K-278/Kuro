"""
Stage 2: Speech-to-Text — Groq Whisper API (primary) + local fallback

Primary:  Groq whisper-large-v3-turbo  -> ~200-400ms, same accuracy
Fallback: local faster-whisper          -> used if Groq fails/offline

The Groq path:
  1. Collect all PCM frames from audio_stream
  2. Convert int16 -> float32 -> WAV bytes in memory
  3. POST to Groq /openai/v1/audio/transcriptions  (async, non-blocking)
  4. Return transcript in ~300ms total

Set STT_BACKEND=local in .env to force local Whisper.
Groq key is read from GROQ_API_KEY (already in your .env).
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import wave
from pathlib import Path
from typing import AsyncGenerator, Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)


# -- Load contacts for vocabulary injection -----------------------------------
def _load_contact_names() -> list[str]:
    """Load all contact names/aliases from contacts.json for STT vocabulary."""
    contacts_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "contacts", "contacts.json"
    )
    names: list[str] = []
    try:
        if os.path.exists(contacts_path):
            with open(contacts_path, encoding="utf-8") as f:
                data = json.load(f)
            for key, val in data.items():
                names.append(key.title())
                if val.get("name"):
                    names.append(val["name"])
                if val.get("whatsapp_name"):
                    names.append(val["whatsapp_name"])
                for alias in val.get("aliases", []):
                    names.append(alias.title())
    except Exception as e:
        logger.debug(f"[STT] Could not load contacts for vocab: {e}")
    # Deduplicate while preserving order
    seen = set()
    unique = []
    for n in names:
        nl = n.lower()
        if nl not in seen:
            seen.add(nl)
            unique.append(n)
    return unique

_CONTACT_NAMES = _load_contact_names()


# -- Ensure NVIDIA DLLs are discoverable BEFORE ctranslate2 loads ------------
def _add_nvidia_dll_dirs() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes
        import sys
        site_packages = Path(sys.prefix) / "Lib" / "site-packages"
        nvidia_root = site_packages / "nvidia"

        # Ensure Windows loader can resolve dependent DLLs from wheel-installed paths.
        dll_dirs = [
            nvidia_root / "cuda_runtime" / "bin",
            nvidia_root / "cublas" / "bin",
            nvidia_root / "cuda_nvrtc" / "bin",
            nvidia_root / "cudnn" / "bin",
        ]
        for d in dll_dirs:
            if d.exists():
                try:
                    os.add_dll_directory(str(d))
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).debug(f"Non-critical error: {e}")

        # Also prepend PATH for libraries that internally load dependencies by name.
        dll_path_prefix = ";".join([str(d) for d in dll_dirs if d.exists()])
        if dll_path_prefix:
            os.environ["PATH"] = dll_path_prefix + ";" + os.environ.get("PATH", "")

        for dll in [
            nvidia_root / "cuda_runtime" / "bin" / "cudart64_12.dll",
            nvidia_root / "cublas" / "bin" / "cublas64_12.dll",
            nvidia_root / "cublas" / "bin" / "cublasLt64_12.dll",
            nvidia_root / "cuda_nvrtc" / "bin" / "nvrtc64_120_0.dll",
            nvidia_root / "cudnn" / "bin" / "cudnn_ops_infer64_8.dll",
            nvidia_root / "cudnn" / "bin" / "cudnn_cnn_infer64_8.dll",
            nvidia_root / "cudnn" / "bin" / "cudnn_adv_infer64_8.dll",
        ]:
            if dll.exists():
                ctypes.CDLL(str(dll))
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug(f"Non-critical error: {e}")


_add_nvidia_dll_dirs()


# -- Vocabulary prompt --------------------------------------------------------
_CONTACT_HINT = ", ".join(_CONTACT_NAMES) if _CONTACT_NAMES else ""
_PROMPT = (
    "KURO voice assistant commands. "
    "WhatsApp, Spotify, YouTube, Chrome, VS Code, Discord, Telegram. "
    "Send message, play music, open app, search Google. "
    "Indian English, Tamil accent. "
    + (f"Contact names: {_CONTACT_HINT}. " if _CONTACT_HINT else "")
)
logger.info(f"[STT] Whisper prompt includes {len(_CONTACT_NAMES)} contact names")


# -- Hallucination filter -----------------------------------------------------
_HALLUCINATIONS = {
    "", " ", ".", "..", "...", "。",
    "thank you", "thank you.", "thanks", "thanks.",
    "you", "bye", "bye.", "goodbye", "goodbye.",
    "ok", "okay", "uh", "um", "hmm", "hm",
    "silence", "background noise", "music", "music playing",
    "i'm sorry", "i'm sorry.", "i am sorry", "i am sorry.",
}


# -- Post-processing corrections ---------------------------------------------
_CORRECTIONS = [
    (re.compile(r"\bwhat'?s\s*app\b", re.I), "WhatsApp"),
    (re.compile(r"\bwhat\s+sapp\b", re.I), "WhatsApp"),
    (re.compile(r"\byou\s*tube\b", re.I), "YouTube"),
    (re.compile(r"\bvs?\.?\s*code\b", re.I), "VS Code"),
    (re.compile(r"\bspot\s*if\s*y\b", re.I), "Spotify"),
    (re.compile(r"\btelgram\b", re.I), "Telegram"),
    (re.compile(r"\bdis\s*cord\b", re.I), "Discord"),
    (re.compile(r"\bopen\s+up\b", re.I), "open"),
    (re.compile(r"\bclose\s+down\b", re.I), "close"),
    (re.compile(r"\bsend\s+a\s+message\b", re.I), "send message"),
    # Contact name corrections (common STT misrecognitions)
    (re.compile(r"\b(?:Keith|Kirth|Kirtana|Kirthan|Kirthana|Keerth)\b", re.I), "Keerthana"),
    (re.compile(r"\b(?:Goutam|Goutom|Gotham|Gautam|Gowtham|Goutham)\b", re.I), "Goutham"),
    (re.compile(r"\b(?:Sajjath|Sajad|Sajat|Sujjad)\b", re.I), "Sajjad"),
    # Tamil accent artifacts
    (re.compile(r"\s+\b(da|di|na|ma|ra|pa|bro|machan|yaar|anna|akka)\s*$", re.I), ""),
    (re.compile(r"\s{2,}"), " "),
]


def _dedup_transcript(text: str) -> str:
    """
    Strip duplicate/near-duplicate sentences from Whisper output.

    Handles cases like:
      'Send hi to X on WhatsApp. Send hi to X on WhatsApp.'  (exact dup)
      'Send hi to Keith on WhatsApp. Send hi to Keith.'       (partial dup)
    """
    if not text:
        return text
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    if len(sentences) <= 1:
        return text

    from difflib import SequenceMatcher
    unique = [sentences[0]]
    for s in sentences[1:]:
        s_clean = s.lower().strip().rstrip(".")
        is_dup = False
        for u in unique:
            u_clean = u.lower().strip().rstrip(".")
            # Check: is one a substring of the other?
            if s_clean in u_clean or u_clean in s_clean:
                is_dup = True
                break
            # Check: high similarity ratio
            ratio = SequenceMatcher(None, s_clean, u_clean).ratio()
            if ratio > 0.65:
                is_dup = True
                break
        if not is_dup:
            unique.append(s)

    result = " ".join(unique)
    if result != text:
        logger.info(f"[STT] Dedup: '{text[:60]}' → '{result[:60]}'")
    return result


def _post_process(text: str) -> str:
    # First: strip duplicate sentences
    text = _dedup_transcript(text)
    # Then: apply corrections
    for pat, rep in _CORRECTIONS:
        text = pat.sub(rep, text)
    return text.strip()


def _is_hallucination(text: str) -> bool:
    t = text.lower().strip().rstrip(".")
    return t in _HALLUCINATIONS or len(t) <= 1


# -- PCM -> WAV bytes (in-memory) --------------------------------------------
def _pcm_to_wav_bytes(audio_f32: np.ndarray, sample_rate: int = 16000) -> bytes:
    pcm_i16 = (audio_f32 * 32767).clip(-32768, 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_i16.tobytes())
    return buf.getvalue()


# -- Groq Whisper transcription ----------------------------------------------
_groq_client = None

async def _transcribe_groq(audio_f32: np.ndarray) -> str:
    import io
    import scipy.io.wavfile as wav
    import httpx
    
    global _groq_client
    if _groq_client is None:
        _groq_client = httpx.AsyncClient(timeout=8.0)

    # Convert to 16kHz PCM WAV in memory
    audio_int16 = (audio_f32 * 32767).astype(np.int16)
    wav_io = io.BytesIO()
    wav.write(wav_io, 16000, audio_int16)
    wav_bytes = wav_io.getvalue()

    api_key = os.getenv("GROQ_API_KEY", "")
    if not api_key:
        raise ValueError("GROQ_API_KEY not set")

    resp = await _groq_client.post(
        "https://api.groq.com/openai/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {api_key}"},
        files={"file": ("audio.wav", wav_bytes, "audio/wav")},
        data={
            "model": "whisper-large-v3-turbo",
            "language": "en",
            "prompt": _PROMPT,
            "response_format": "text",
            "temperature": "0",
        },
    )
    resp.raise_for_status()
    return resp.text.strip()


# -- Local Whisper transcription (fallback) ----------------------------------
def _transcribe_local(audio_f32: np.ndarray) -> str:
    from core.model_manager import model_manager

    model = model_manager.get_sync("whisper")
    segments, _ = model.transcribe(
        audio_f32,
        beam_size=1,
        language="en",
        initial_prompt=_PROMPT,
        condition_on_previous_text=False,
        without_timestamps=True,
        vad_filter=False,
        no_speech_threshold=0.6,
        compression_ratio_threshold=2.4,
    )
    return " ".join(seg.text for seg in segments).strip()


def _register_local_model() -> None:
    try:
        from core.model_manager import model_manager

        def _loader():
            from faster_whisper import WhisperModel
            from core.gpu_detect import effective_device, compute_type_for_device
            model_name = os.getenv("WHISPER_MODEL", "large-v3-turbo")
            # Auto-detect GPU — if GPU is off, effective_device returns 'cpu' automatically
            device = effective_device(os.getenv("STT_DEVICE", "cuda"))
            compute = compute_type_for_device(device)
            try:
                m = WhisperModel(model_name, device=device, compute_type=compute)
                list(m.transcribe(np.zeros(16000, dtype=np.float32), beam_size=1)[0])
                logger.info(f"[STT] Local Whisper {model_name} loaded on {device} ({compute})")
                return m
            except Exception as e:
                logger.warning(f"[STT] {device.upper()} failed ({e}), falling back to CPU")
                m = WhisperModel(model_name, device="cpu", compute_type="int8")
                logger.info(f"[STT] Local Whisper {model_name} loaded on cpu (int8)")
                return m

        model_manager.register("whisper", _loader)
    except Exception as e:
        logger.debug(f"[STT] Could not register local model: {e}")


_register_local_model()


class StreamingSTT:
    """Groq Whisper by default with local faster-whisper fallback."""

    def __init__(self):
        self._groq_key = os.getenv("GROQ_API_KEY", "").strip()
        self._stt_backend = os.getenv("STT_BACKEND", "groq").lower()
        
        # Validate at startup — fail loudly, not silently
        if self._stt_backend == "groq" and not self._groq_key:
            logger.critical(
                "GROQ_API_KEY is not set in .env. "
                "Either set the key or switch STT_BACKEND=local in .env"
            )
            # Fall back to local instead of crashing
            self._stt_backend = "local"
            logger.warning("STT falling back to local faster-whisper")
        
        self._use_groq = self._stt_backend == "groq"
        self._last_audio: Optional[np.ndarray] = None
        backend = "Groq Whisper API" if self._use_groq else "local faster-whisper"
        logger.info(f"[STT] StreamingSTT ready — backend: {backend}")

    async def transcribe(
        self,
        audio_stream: AsyncGenerator[np.ndarray, None],
        on_partial: Optional[Callable[[str], None]] = None,
    ) -> str:
        chunks: list[np.ndarray] = []
        async for frame in audio_stream:
            chunks.append(frame)

        if not chunks:
            logger.debug("[STT] No audio frames received")
            return ""

        audio_f32 = np.concatenate(chunks).astype(np.float32) / 32768.0
        self._last_audio = audio_f32
        duration = len(audio_f32) / 16000
        logger.debug(f"[STT] Audio collected: {duration:.2f}s")

        if duration < 0.2:
            logger.debug(f"[STT] Audio too short ({duration:.2f}s) — skipping")
            return ""

        import time as _time
        t0 = _time.monotonic()
        raw = ""

        if self._use_groq:
            try:
                raw = await _transcribe_groq(audio_f32)
                elapsed = _time.monotonic() - t0
                logger.info(f"[STT] Groq transcribed in {elapsed:.2f}s: '{raw}'")
            except Exception as e:
                logger.warning(f"[STT] Groq failed ({type(e).__name__}: {e}). Skipping CPU fallback to avoid 20s delay.")
                raw = ""
        else:
            loop = asyncio.get_event_loop()
            raw = await loop.run_in_executor(None, _transcribe_local, audio_f32)
            elapsed = _time.monotonic() - t0
            logger.info(f"[STT] Local transcribed in {elapsed:.2f}s: '{raw}'")

        if _is_hallucination(raw):
            logger.debug(f"[STT] Filtered hallucination: '{raw}'")
            # One silent retry on Groq — sometimes the first request returns empty
            # due to a transient API quirk; the second almost always works.
            if self._use_groq and not raw:
                try:
                    import asyncio as _a2
                    await _a2.sleep(0.15)
                    raw2 = await _transcribe_groq(audio_f32)
                    if raw2 and not _is_hallucination(raw2):
                        logger.info(f"[STT] Retry recovered: '{raw2}'")
                        raw = raw2
                    else:
                        return ""
                except Exception:
                    return ""
            else:
                return ""

        final = _post_process(raw)
        logger.info(f"[STT] final='{final}'")

        if on_partial and final:
            if asyncio.iscoroutinefunction(on_partial):
                await on_partial(final)
            else:
                on_partial(final)

        return final
