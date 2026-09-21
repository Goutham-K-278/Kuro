"""
KURO - Central Configuration
All settings pulled from .env with safe defaults
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ─── Project Root ────────────────────────────────────────────────────────────
ROOT_DIR    = Path(__file__).parent.parent
DATA_DIR    = ROOT_DIR / "data"
RAG_DIR     = ROOT_DIR / "rag" / "documents"
WATCH_DIR   = ROOT_DIR / os.getenv("BLENDER_WATCH_DIR", "data/blender_watch")

# ─── Local LLM (Ollama) ──────────────────────────────────────────────────────
# Default: gemma4:e4b — Google Gemma 4 Effective-4B (April 2026)
# VRAM: ~3.0-3.5 GB | Context: 128K | Strong reasoning + JSON output
OLLAMA_BASE_URL  = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
LOCAL_MODEL      = os.getenv("LOCAL_MODEL",    "gemma2:2b")   # BUG #8 FIX: was gemma4:e4b
FALLBACK_MODEL   = os.getenv("FALLBACK_MODEL", "gemma2:2b")   # BUG #8 FIX: was gemma4:e4b
EMBED_MODEL      = os.getenv("EMBED_MODEL",    "nomic-embed-text")

# Speed tuning (lower `LOCAL_NUM_PREDICT` = faster replies)
LOCAL_TEMPERATURE = float(os.getenv("LOCAL_TEMPERATURE", "0.4"))
LOCAL_NUM_PREDICT = int(os.getenv("LOCAL_NUM_PREDICT", "100"))
LOCAL_NUM_CTX = int(os.getenv("LOCAL_NUM_CTX", "8192"))
LOCAL_TOP_P = float(os.getenv("LOCAL_TOP_P", "0.9"))

# ─── Cloud LLM (OpenRouter) ──────────────────────────────────────────────────
CLOUD_API_URL    = os.getenv("CLOUD_API_URL",  "https://openrouter.ai/api/v1/chat/completions")
CLOUD_API_KEY    = os.getenv("CLOUD_API_KEY",  "")
CLOUD_MODEL      = os.getenv("CLOUD_MODEL",    "qwen/qwen3-coder:free")

# Force everything local for max speed (disables cloud routing)
FORCE_LOCAL = os.getenv("FORCE_LOCAL", "false").lower() == "true"

# ─── Memory ──────────────────────────────────────────────────────────────────
MAX_HISTORY      = 20       # Messages kept in short-term memory
CHROMA_DIR       = DATA_DIR / "chroma_db"

# ─── GPU Auto-Detection ─────────────────────────────────────────────────────
# Reads GPU_DISABLED env var; returns 'cuda' or 'cpu' automatically.
# Every component (STT, TTS, Vision) reads from gpu_detect, not hard-coded strings.
from core.gpu_detect import GPU_AVAILABLE, effective_device as _eff_device

# ─── Voice ───────────────────────────────────────────────────────────────────
# distil-large-v3: high-accuracy distilled Whisper with lower VRAM and faster decode.
# Optimized default for your 4050 budget and noisy room handling.
WHISPER_MODEL    = os.getenv("WHISPER_MODEL", "distil-large-v3")
WHISPER_BEAM_SIZE = int(os.getenv("WHISPER_BEAM_SIZE", "1"))   # beam=1 = greedy = fastest
WHISPER_LANGUAGE  = os.getenv("WHISPER_LANGUAGE", "en")        # force English; avoids lang-detect delay
# STT_DEVICE: auto-detected from GPU at startup — overridable in .env as STT_DEVICE=cuda|cpu
# If no GPU is found, automatically falls back to cpu even if .env says cuda.
_stt_device_pref  = os.getenv("STT_DEVICE", "cuda")  # what the user wants
STT_DEVICE        = _eff_device(_stt_device_pref)      # what we'll actually use
STT_BACKEND      = os.getenv("STT_BACKEND", "whisper")  # "whisper" | "sherpa"
TTS_BACKEND      = os.getenv("TTS_BACKEND", "edge")  # "kokoro" | "edge" | "pyttsx3"
KOKORO_VOICE     = os.getenv("KOKORO_VOICE", "af_heart")  # Kokoro voice preset
WAKE_WORD        = "kuro"
MIC_SAMPLE_RATE  = 16000
ALERT_TIMEOUT    = int(os.getenv("ALERT_TIMEOUT", "8"))   # seconds to stay alert after a response

# ─── Context Window (prevents slowdown after many conversations) ─────────────
CONTEXT_WINDOW_SIZE = int(os.getenv("CONTEXT_WINDOW_SIZE", "10"))  # messages sent to LLM
SUMMARIZE_EVERY_N   = int(os.getenv("SUMMARIZE_EVERY_N",  "20"))  # trigger summarization

# Real-time VAD mic cutoff
STT_VAD_THRESHOLD     = float(os.getenv("STT_VAD_THRESHOLD",     "0.30"))  # catches soft Tamil-accented word endings
STT_SILENCE_MS        = int(os.getenv("VAD_SILENCE_THRESHOLD_MS", "1800"))
STT_PRE_SPEECH_MS     = int(os.getenv("STT_PRE_SPEECH_MS",       "200"))   # 200ms pre-roll (was 250)
STT_MAX_RECORD_SECONDS= int(os.getenv("STT_MAX_RECORD_SECONDS",  "30"))
STT_INITIAL_TIMEOUT_S = float(os.getenv("STT_INITIAL_TIMEOUT_S", "3.0"))
# 0.50 means only frames above 50% of silence-threshold are forwarded to STT.
STT_STREAM_GATE_MULT  = float(os.getenv("STT_STREAM_GATE_MULT", "0.50"))
# 0.85 lowers direct-arm trigger slightly below calibrated voice threshold
# to improve pickup of soft close-mic speech in noisy rooms.
STT_DIRECT_ARM_MULT   = float(os.getenv("STT_DIRECT_ARM_MULT", "0.85"))

# Noise gate: discard captured audio below this RMS energy level.
# Background chatter from across a classroom has low energy at the mic.
# Only close-mic speech (user talking into the laptop) passes this gate.
# Raise this value (400-600) in very noisy rooms; lower (100-200) in quiet rooms.
STT_MIN_RMS       = int(os.getenv("STT_MIN_RMS", "0"))

# Maximum voice energy threshold — cap the auto-calibrated value so close-mic
# speech ALWAYS triggers, even in noisy classrooms where ambient P90 is high.
# 800 allows calibration to go high enough for noisy rooms while still firing
# on close-mic speech (normal mic RMS for speech = 600-2000).
STT_VOICE_ENERGY_MAX = float(os.getenv("STT_VOICE_ENERGY_MAX", "1200.0"))

# How many frames with no speech_seen before aborting a capture attempt.
# 2.5s * 16000 / 512 ≈ 78 frames — gives more time than the old 1.5s.
STT_ABORT_NO_SPEECH_FRAMES = int(os.getenv("STT_ABORT_NO_SPEECH_FRAMES", "50"))

# Idle timeout: seconds of no speech after greeting before returning to calm/idle.
# In classroom: 5s is good — you have time to think but Kuro doesn't stay armed
# forever picking up random noise.
STT_IDLE_TIMEOUT_S = float(os.getenv("STT_IDLE_TIMEOUT_S", "5.0"))
STT_TRANSCRIBE_TIMEOUT_S = float(os.getenv("STT_TRANSCRIBE_TIMEOUT_S", "45.0"))

# Whisper VAD knobs for noisy environments.
# Raise threshold to be stricter in noisy rooms; lower if speech is being clipped.
WHISPER_VAD_THRESHOLD        = float(os.getenv("WHISPER_VAD_THRESHOLD", "0.40"))
WHISPER_VAD_MIN_SILENCE_MS   = int(os.getenv("WHISPER_VAD_MIN_SILENCE_MS", "450"))
WHISPER_VAD_SPEECH_PAD_MS    = int(os.getenv("WHISPER_VAD_SPEECH_PAD_MS", "220"))

# Noise reduction adds 100-300 ms latency — off by default for speed.
# Enable with STT_USE_NOISE_REDUCE=true in .env if you're in a noisy room.
STT_USE_NOISE_REDUCE = os.getenv("STT_USE_NOISE_REDUCE", "false").lower() == "true"

# ─── OpenWakeWord ────────────────────────────────────────────────────────────
OWW_MODEL_PATH   = str(ROOT_DIR / "voice" / "models" / "kuro.onnx")
OWW_THRESHOLD    = float(os.getenv("OWW_THRESHOLD", "0.80"))  # raised for noisy environments; lower to 0.6 for quiet rooms
# Set WAKE_WORD_ENABLED=true in .env to enable always-on "Hey Kuro" mic detection.
# When false (default), mic stays closed during standby — use Ctrl+Shift+K instead.
WAKE_WORD_ENABLED = os.getenv("WAKE_WORD_ENABLED", "false").lower() == "true"
OWW_INFERENCE_FRAMEWORK = "onnx"

# ─── Blender ─────────────────────────────────────────────────────────────────
BLENDER_PATH     = os.getenv("BLENDER_PATH", "blender")
BLENDER_TIMEOUT  = 60      # Max seconds to wait for render

# ─── UI ──────────────────────────────────────────────────────────────────────
APP_PORT         = int(os.getenv("APP_PORT", 7860))
APP_DEBUG        = os.getenv("APP_DEBUG", "false").lower() == "true"

# ─── Logging ─────────────────────────────────────────────────────────────────
LOG_LEVEL        = os.getenv("LOG_LEVEL", "INFO")

# ─── System Prompts ──────────────────────────────────────────────────────────
SYSTEM_PROMPT_GENERAL = """You are kuro — a local AI assistant. You are concise, precise, and intelligent.
Answer in plain text unless code is needed. Prefer short responses unless detailed explanation is requested."""

SYSTEM_PROMPT_CODING = """You are kuro — an expert coding assistant. 
Write clean, well-commented Python code. Always explain what the code does briefly."""

SYSTEM_PROMPT_FAST = """You are kuro. Be extremely brief — one or two sentences max. No fluff."""

SYSTEM_PROMPT_BLENDER = """You are kuro — a Blender Python (bpy) expert.
Generate complete, runnable Blender scripts. Use bpy API only. No subprocess, no os module, no network calls."""

SYSTEM_PROMPT_DSA = """You are kuro — a Data Structures & Algorithms tutor.
Teach clearly with examples, step-by-step logic, and Python code. Use analogies when helpful."""

# -- Vision (Florence-2) ------------------------------------------------------
VISION_ENABLED = os.getenv("VISION_ENABLED", "true").lower() == "true"
FLORENCE_MODEL = os.getenv("FLORENCE_MODEL", "microsoft/Florence-2-base")
FLORENCE_DEVICE = os.getenv("FLORENCE_DEVICE", "auto")  # auto | cuda | cpu
FLORENCE_QUANTIZE = os.getenv("FLORENCE_QUANTIZE", "int8")  # int8 | fp16 | fp32
VISION_CAPTURE_CACHE_MS = int(os.getenv("VISION_CAPTURE_CACHE_MS", "500"))
VISION_VERIFY_TIMEOUT_S = float(os.getenv("VISION_VERIFY_TIMEOUT_S", "2.0"))
VISION_PRE_ACTION = os.getenv("VISION_PRE_ACTION", "true").lower() == "true"
VISION_POST_VERIFY = os.getenv("VISION_POST_VERIFY", "true").lower() == "true"

# -- Security / Privacy --------------------------------------------------------
SECURITY_LEVEL = os.getenv("SECURITY_LEVEL", "high").lower()  # low | medium | high
PRIVACY_GUARD_ENABLED = os.getenv("PRIVACY_GUARD_ENABLED", "true").lower() == "true"
MEMORY_ENCRYPTION_ENABLED = os.getenv("MEMORY_ENCRYPTION_ENABLED", "true").lower() == "true"

# -- TurboQuant (best-effort) -------------------------------------------------
TURBOQUANT_ENABLED = os.getenv("TURBOQUANT_ENABLED", "true").lower() == "true"
TURBOQUANT_MODE = os.getenv("TURBOQUANT_MODE", "auto").lower()  # auto | on | off
