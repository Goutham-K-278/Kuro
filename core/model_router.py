"""
KURO - Model Router
Decides: should this task go to LOCAL (fast) or CLOUD (powerful)?
"""
import logging
from dataclasses import dataclass
from enum import Enum

from core.config import FORCE_LOCAL

logger = logging.getLogger(__name__)


class Intent(str, Enum):
    # System tasks → LOCAL
    GENERAL_CHAT     = "general_chat"
    SYSTEM_COMMAND   = "system_command"
    FILE_OPERATION   = "file_operation"
    APP_CONTROL      = "app_control"
    SYSTEM_INFO      = "system_info"
    MEMORY_RECALL    = "memory_recall"
    SETTINGS         = "settings"

    # DSA / code tasks → CLOUD
    TEACH_DSA        = "teach_dsa"
    CODE_HELP        = "code_help"
    CODE_REVIEW      = "code_review"

    # Blender tasks → CLOUD
    BLENDER_CREATE   = "blender_create"
    BLENDER_MODIFY   = "blender_modify"

    # Web
    WEB_SEARCH       = "web_search"

    UNKNOWN          = "unknown"


# These intents ALWAYS go to cloud (need the big brain)
CLOUD_INTENTS = {
    Intent.TEACH_DSA,
    Intent.CODE_REVIEW,
    Intent.BLENDER_CREATE,
    Intent.BLENDER_MODIFY,
}

# These intents ALWAYS go local (fast, simple)
LOCAL_INTENTS = {
    Intent.SYSTEM_COMMAND,
    Intent.FILE_OPERATION,
    Intent.APP_CONTROL,
    Intent.SYSTEM_INFO,
    Intent.MEMORY_RECALL,
    Intent.SETTINGS,
}

# Keywords that force cloud routing regardless of intent
CLOUD_KEYWORDS = [
    "explain in detail", "teach me", "deep dive", "algorithm", "complexity",
    "time complexity", "space complexity", "dynamic programming", "recursion",
    "blender script", "bpy", "3d scene", "generate a mesh", "shader",
    "big o", "proof", "derive", "mathematically",
]

# Keywords that force local routing
LOCAL_KEYWORDS = [
    "open", "close", "screenshot", "volume", "brightness", "shutdown",
    "restart", "what time", "battery", "clipboard", "what is today",
]


@dataclass
class RoutingDecision:
    use_cloud: bool
    reason: str
    mode: str       # prompt mode: "general", "dsa", "coding", "blender", "fast"
    intent: Intent


def route(user_message: str, intent: Intent, message_length: int = 0) -> RoutingDecision:
    """
    Decides whether to use LOCAL or CLOUD model for a given request.

    Rules (in priority order):
    1. Explicit cloud intents → cloud
    2. Explicit local intents → local
    3. Cloud keywords in message → cloud
    4. Local keywords in message → local
    5. CODE_HELP: short snippets → local, long/complex → cloud
    6. GENERAL_CHAT: always local
    7. Default → local
    """
    if FORCE_LOCAL:
        return RoutingDecision(
            use_cloud=False,
            reason="FORCE_LOCAL enabled — routing everything locally",
            mode=_intent_to_mode(intent),
            intent=intent,
        )

    msg_lower = user_message.lower()

    # Rule 1: Hard cloud intents
    if intent in CLOUD_INTENTS:
        mode = _intent_to_mode(intent)
        return RoutingDecision(
            use_cloud=True,
            reason=f"Intent '{intent.value}' requires cloud brain",
            mode=mode,
            intent=intent,
        )

    # Rule 2: Hard local intents
    if intent in LOCAL_INTENTS:
        return RoutingDecision(
            use_cloud=False,
            reason=f"Intent '{intent.value}' handled locally",
            mode="fast",
            intent=intent,
        )

    # Rule 3: Cloud keyword in message
    for kw in CLOUD_KEYWORDS:
        if kw in msg_lower:
            return RoutingDecision(
                use_cloud=True,
                reason=f"Cloud keyword detected: '{kw}'",
                mode=_intent_to_mode(intent),
                intent=intent,
            )

    # Rule 4: Local keyword in message
    for kw in LOCAL_KEYWORDS:
        if kw in msg_lower:
            return RoutingDecision(
                use_cloud=False,
                reason=f"Local keyword detected: '{kw}'",
                mode="fast",
                intent=intent,
            )

    # Rule 5: CODE_HELP — complexity check
    if intent == Intent.CODE_HELP:
        if message_length > 200 or any(kw in msg_lower for kw in ["optimize", "refactor", "review", "why is this"]):
            return RoutingDecision(
                use_cloud=True,
                reason="Complex code task — cloud for quality",
                mode="coding",
                intent=intent,
            )
        return RoutingDecision(
            use_cloud=False,
            reason="Simple code snippet — handled locally",
            mode="coding",
            intent=intent,
        )

    # Rule 6 & Default: General chat → local
    return RoutingDecision(
        use_cloud=False,
        reason="Default: local brain sufficient",
        mode="general",
        intent=intent,
    )


def _intent_to_mode(intent: Intent) -> str:
    """Map intent to the correct system prompt mode."""
    mapping = {
        Intent.TEACH_DSA:      "dsa",
        Intent.CODE_HELP:      "coding",
        Intent.CODE_REVIEW:    "coding",
        Intent.BLENDER_CREATE: "blender",
        Intent.BLENDER_MODIFY: "blender",
        Intent.GENERAL_CHAT:   "general",
        Intent.SYSTEM_COMMAND: "fast",
    }
    return mapping.get(intent, "general")


# ═══════════════════════════════════════════════════════════════════════════════
# LLM BACKEND SWITCHER — Groq (cloud) ↔ Ollama local model
# Pings Groq every 30 seconds, auto-switches when connectivity changes.
# ═══════════════════════════════════════════════════════════════════════════════

import asyncio
import time
import subprocess
from enum import Enum as _Enum

try:
    import aiohttp as _aiohttp
    _AIOHTTP_AVAILABLE = True
except ImportError:
    _AIOHTTP_AVAILABLE = False


class LLMBackend(_Enum):
    GROQ  = "groq"
    LOCAL = "local"


_current_backend  = LLMBackend.GROQ
_last_check_time  = 0.0
_CHECK_INTERVAL   = 30   # Re-check every 30 seconds
_manual_override: LLMBackend | None = None   # None = auto; set via /api/llm-mode

# ── VRAM Monitor ────────────────────────────────────────────────────────────

def get_free_vram_mb() -> int:
    """Returns free VRAM in MB. Fast — just reads nvidia-smi."""
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            timeout=1
        ).decode().strip()
        return int(out)
    except Exception:
        return 9999  # Assume plenty if we can't check

def is_vram_under_pressure() -> bool:
    """True if less than 800MB free — time to be careful."""
    return get_free_vram_mb() < 800

# ── Routing Logic ────────────────────────────────────────────────────────────

def set_backend_override(backend: "LLMBackend | None") -> None:
    """
    Manually pin the LLM backend.  Pass None to restore auto-detection.
    Exposed via POST /api/llm-mode {"mode": "auto"|"groq"|"local"}.
    """
    global _manual_override, _current_backend, _last_check_time
    _manual_override = backend
    if backend is not None:
        _current_backend = backend
    _last_check_time = 0.0   # Force re-check when override is removed
    label = backend.value if backend else "auto"
    logger.info(f"[ModelRouter] Manual override → {label}")


def get_backend_status() -> dict:
    """Return current backend state — used by GET /api/llm-mode."""
    active = _manual_override if _manual_override is not None else _current_backend
    free_vram = get_free_vram_mb()
    return {
        "mode":     "auto" if _manual_override is None else _manual_override.value,
        "active":   active.value,
        "override": _manual_override is not None,
        "free_vram_mb": free_vram,
        "vram_pressure": free_vram < 800
    }


async def _ping_groq(timeout: float = 1.5) -> bool:
    """Quick HEAD request to verify Groq API is reachable."""
    if not _AIOHTTP_AVAILABLE:
        # Fallback: use built-in urllib (sync, run in executor)
        import urllib.request
        import asyncio as _a
        loop = _a.get_event_loop()
        def _check():
            try:
                urllib.request.urlopen("https://api.groq.com", timeout=int(timeout))
                return True
            except Exception:
                return False
        return await loop.run_in_executor(None, _check)

    try:
        async with _aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.groq.com",
                timeout=_aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                return True
    except Exception:
        return False


async def get_llm_backend() -> LLMBackend:
    """
    Returns which LLM backend to use RIGHT NOW.
    Respects manual override if set; otherwise auto-detects every 30 s.
    """
    global _current_backend, _last_check_time

    # Manual override takes highest priority
    if _manual_override is not None:
        return _manual_override

    now = time.time()
    if now - _last_check_time > _CHECK_INTERVAL:
        _last_check_time = now
        online = await _ping_groq()
        _current_backend = LLMBackend.GROQ if online else LLMBackend.LOCAL
        status = "Groq (cloud)" if online else "Local Ollama (offline mode)"
        logger.info(f"[ModelRouter] LLM backend (auto): {status}")

    return _current_backend

