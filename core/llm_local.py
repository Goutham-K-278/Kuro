"""
KURO — Local LLM Engine (Ollama)
================================
Runs local LLM requests against Ollama API.
Default model: gemma4:e4b (Google Gemma 4 Effective-4B)
  - Released: April 2026
  - VRAM:     ~3.0–3.5 GB (fits RTX 4050 6 GB budget)
  - Strengths: Strong reasoning, JSON output, 128K context
"""

import aiohttp
import asyncio
import os
import logging
from core.config import (
    OLLAMA_BASE_URL, LOCAL_MODEL,
    LOCAL_NUM_CTX, LOCAL_NUM_PREDICT, LOCAL_TEMPERATURE, LOCAL_TOP_P
)

logger = logging.getLogger(__name__)

# Sole model: gemma4:e4b
_MODEL_PREFERENCE = [
    "gemma4:e4b",
]
_available_model_cache: str | None = None


async def _pick_model() -> str:
    """Pick the lightest available Ollama model from the preference list."""
    global _available_model_cache
    if _available_model_cache:
        return _available_model_cache

    env_model = os.getenv("LOCAL_MODEL", LOCAL_MODEL)
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{OLLAMA_BASE_URL}/api/tags",
                timeout=aiohttp.ClientTimeout(total=3)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    installed = {m["name"] for m in data.get("models", [])}
                    for candidate in _MODEL_PREFERENCE:
                        if candidate in installed:
                            _available_model_cache = candidate
                            logger.info(f"[LocalLLM] Using model: {candidate}")
                            return candidate
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug(f"Non-critical error: {e}")

    # Fall back to env/config value
    _available_model_cache = env_model
    return env_model

async def complete_local(prompt: str, max_tokens: int = 256) -> str:
    """Send an async request to the Ollama backend."""
    model = await _pick_model()

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are Kuro. Reply in JSON only. Be concise."},
            {"role": "user", "content": prompt}
        ],
        "stream": False,
        "options": {
            "temperature": 0.1,
            "top_p": 0.9,
            "num_predict": max_tokens,
            "num_ctx": 2048,
        },
        "keep_alive": "5m", 
    }

    try:
        # Re-using aiohttp correctly for API call
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{OLLAMA_BASE_URL}/api/chat", json=payload, timeout=aiohttp.ClientTimeout(total=60)
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                result = data["message"]["content"].strip()
                logger.debug(f"[LocalLLM/Ollama] Generated response.")
                return result
    except aiohttp.ClientConnectorError as e:
        logger.error(f"Local chat failed: Ollama is unreachable at {OLLAMA_BASE_URL} ({e})")
        raise RuntimeError("Local model unavailable: Ollama service is offline.")
    except aiohttp.ClientResponseError as e:
        if e.status == 404:
            logger.error(f"Local chat failed: model '{model}' not found in Ollama.")
            raise RuntimeError(
                f"Local model '{model}' is missing. Run: ollama pull {model}"
            )
        logger.error(f"Local chat failed with HTTP {e.status}: {e}")
        raise RuntimeError(f"Local model request failed (HTTP {e.status}).")
    except Exception as e:
        logger.error(f"Local chat failed: {e}")
        raise RuntimeError("Local model request failed unexpectedly.")
