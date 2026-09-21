"""
voice/speaker_verify.py
─────────────────────────────────────────────────────────────────
Speaker verification — ensures only the enrolled owner's voice
triggers Kuro's action pipeline.

Uses Resemblyzer 256-dim cosine similarity against a stored voiceprint.
Runs on CPU in ~50ms — negligible overhead vs STT + LLM.

Activation:
  Set SPEAKER_VERIFY=true and SPEAKER_VERIFY_THRESHOLD=0.72 in .env.
  Run scripts/enroll_voice.py first to create the voiceprint.

Threshold guide:
  0.65 — lenient (allows similar family voices)
  0.72 — balanced ✅ recommended
  0.80 — strict (noisy classroom performance may suffer)
─────────────────────────────────────────────────────────────────
"""

import logging
import os

import numpy as np

from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)

VOICEPRINT_PATH = "data/voice_enrollment/owner_voiceprint.npy"
_THRESHOLD      = float(os.getenv("SPEAKER_VERIFY_THRESHOLD", "0.72"))
_ENABLED        = os.getenv("SPEAKER_VERIFY", "false").lower() == "true"

# Lazy-loaded singletons
_encoder    = None
_voiceprint = None


def is_enabled() -> bool:
    """Returns True if speaker verification is active (SPEAKER_VERIFY=true in .env)."""
    return _ENABLED


def _get_encoder():
    global _encoder
    if _encoder is None:
        try:
            from resemblyzer import VoiceEncoder
            _encoder = VoiceEncoder(device="cpu")   # CPU — saves GPU for Whisper
            logger.info("[SpeakerVerify] Resemblyzer encoder loaded (CPU)")
        except ImportError:
            logger.error(
                "[SpeakerVerify] resemblyzer not installed — run: pip install resemblyzer"
            )
            raise
    return _encoder


def _get_voiceprint() -> np.ndarray:
    global _voiceprint
    if _voiceprint is None:
        if not os.path.exists(VOICEPRINT_PATH):
            raise FileNotFoundError(
                f"No voiceprint found at '{VOICEPRINT_PATH}'. "
                "Run: python scripts/enroll_voice.py"
            )
        _voiceprint = np.load(VOICEPRINT_PATH)
        logger.info(f"[SpeakerVerify] Voiceprint loaded from {VOICEPRINT_PATH}")
    return _voiceprint


def is_owner_voice(
    audio_np: np.ndarray,
    sample_rate: int = 16000,
) -> tuple[bool, float]:
    """
    Verify whether the audio belongs to the enrolled owner.

    Args:
        audio_np:    1-D float32 numpy array of the command audio.
        sample_rate: Audio sample rate in Hz (default 16000).

    Returns:
        (is_owner: bool, similarity_score: float)
        similarity_score ranges from 0.0 (different voice) to 1.0 (identical).
    """
    if not _ENABLED:
        # Verification disabled — allow all commands
        return True, 1.0

    try:
        from resemblyzer import preprocess_wav

        encoder    = _get_encoder()
        voiceprint = _get_voiceprint()

        wav   = preprocess_wav(audio_np.astype(np.float32), source_sr=sample_rate)
        embed = encoder.embed_utterance(wav)

        # Cosine similarity
        norm_embed = np.linalg.norm(embed)
        norm_vp    = np.linalg.norm(voiceprint)
        if norm_embed < 1e-10 or norm_vp < 1e-10:
            logger.warning("[SpeakerVerify] Zero-norm embedding — allowing command")
            return True, 0.0

        score = float(np.dot(embed, voiceprint) / (norm_embed * norm_vp))
        owner = score >= _THRESHOLD

        if owner:
            logger.info(f"[SpeakerVerify] ✅ Owner verified (score={score:.3f}, threshold={_THRESHOLD})")
        else:
            logger.warning(
                f"[SpeakerVerify] ❌ Rejected — score={score:.3f} < threshold={_THRESHOLD}"
            )

        return owner, score

    except Exception as e:
        logger.error(f"Speaker verification error: {e}")
        if os.getenv("SPEAKER_VERIFY_STRICT", "false").lower() == "true":
            return False, 0.0   # strict: deny on error
        else:
            logger.warning("Speaker verify failed — allowing (SPEAKER_VERIFY_STRICT=false)")
            return True, 0.0   # permissive but logged with score=0 so you can see it
