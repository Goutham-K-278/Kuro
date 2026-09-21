"""
KURO - Dual Brain LLM Engine
Manages LOCAL (Ollama) + CLOUD (OpenRouter) model interaction
"""
import json
import logging
from typing import Iterator

import httpx
import requests

from core.config import (
    OLLAMA_BASE_URL, LOCAL_MODEL, FALLBACK_MODEL,
    CLOUD_API_URL, CLOUD_API_KEY, CLOUD_MODEL,
    LOCAL_NUM_CTX, LOCAL_NUM_PREDICT, LOCAL_TEMPERATURE, LOCAL_TOP_P,
    SYSTEM_PROMPT_GENERAL, SYSTEM_PROMPT_CODING,
    SYSTEM_PROMPT_FAST, SYSTEM_PROMPT_BLENDER, SYSTEM_PROMPT_DSA
)

logger = logging.getLogger(__name__)


class LLMEngine:
    """Dual Brain: LOCAL Ollama model + CLOUD OpenRouter model"""

    SYSTEM_PROMPTS = {
        "general":  SYSTEM_PROMPT_GENERAL,
        "coding":   SYSTEM_PROMPT_CODING,
        "fast":     SYSTEM_PROMPT_FAST,
        "blender":  SYSTEM_PROMPT_BLENDER,
        "dsa":      SYSTEM_PROMPT_DSA,
    }

    def __init__(self):
        self.local_model    = LOCAL_MODEL
        self.fallback_model = FALLBACK_MODEL
        self.cloud_model    = CLOUD_MODEL
        logger.info(f"LLMEngine ready | LOCAL={self.local_model} | CLOUD={self.cloud_model}")

    # ─── Internet Check ──────────────────────────────────────────────────────
    _online_cache = None
    _last_check = 0

    def is_online(self) -> bool:
        """Check if internet is available for cloud requests (cached for 60s)."""
        import time
        now = time.time()
        if self._online_cache is not None and (now - self._last_check < 60):
            return self._online_cache
        try:
            requests.get("https://1.1.1.1", timeout=1.0)
            self._online_cache = True
        except Exception:
            self._online_cache = False
        self._last_check = now
        return self._online_cache

    def ollama_running(self) -> bool:
        """Check if Ollama daemon is running."""
        try:
            r = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=3)
            return r.status_code == 200
        except Exception:
            return False

    # ─── LOCAL Brain (Ollama) ────────────────────────────────────────────────
    def chat_local(
        self,
        messages: list[dict],
        mode: str = "general",
        model: str | None = None,
    ) -> str:
        """
        Send a chat request to LOCAL Ollama model.
        Returns full response string.
        """
        model = model or self.local_model
        system_prompt = self.SYSTEM_PROMPTS.get(mode, SYSTEM_PROMPT_GENERAL)

        payload = {
            "model": model,
            "messages": [{"role": "system", "content": system_prompt}] + messages,
            "stream": False,
            "options": {
                "temperature": LOCAL_TEMPERATURE,
                "top_p": LOCAL_TOP_P,
                "num_predict": LOCAL_NUM_PREDICT,
                "num_ctx": LOCAL_NUM_CTX,
            },
            "keep_alive": -1,  # Keep model in VRAM permanently (instant responses)
        }

        try:
            resp = requests.post(
                f"{OLLAMA_BASE_URL}/api/chat",
                json=payload,
                timeout=120,
            )
            resp.raise_for_status()
            return resp.json()["message"]["content"].strip()
        except requests.exceptions.ConnectionError:
            logger.error("Ollama not running — is 'ollama serve' active?")
            raise RuntimeError(
                "Ollama offline. Run: ollama serve (and ensure LOCAL_MODEL is pulled)."
            )
        except Exception as e:
            logger.error(f"Local chat failed: {e}")
            raise

    def stream_local(
        self,
        messages: list[dict],
        mode: str = "general",
    ) -> Iterator[str]:
        """
        Stream tokens from LOCAL Ollama model.
        Yields one token-chunk at a time.
        """
        system_prompt = self.SYSTEM_PROMPTS.get(mode, SYSTEM_PROMPT_GENERAL)

        payload = {
            "model": self.local_model,
            "messages": [{"role": "system", "content": system_prompt}] + messages,
            "stream": True,
            "options": {
                "temperature": LOCAL_TEMPERATURE,
                "top_p": LOCAL_TOP_P,
                "num_ctx": LOCAL_NUM_CTX,
            },
            "keep_alive": -1,
        }

        try:
            with requests.post(
                f"{OLLAMA_BASE_URL}/api/chat",
                json=payload,
                stream=True,
                timeout=120,
            ) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if line:
                        chunk = json.loads(line)
                        token = chunk.get("message", {}).get("content", "")
                        if token:
                            yield token
                        if chunk.get("done"):
                            break
        except Exception as e:
            logger.error(f"Stream failed: {e}")
            yield f"\n[Stream error: {e}]"

    # ─── CLOUD Brain (OpenRouter) ────────────────────────────────────────────
    def chat_cloud(
        self,
        messages: list[dict],
        mode: str = "general",
    ) -> str:
        """
        Send a chat request to the configured cloud model via OpenRouter.
        Falls back to the configured local fallback model if offline.
        """
        if not CLOUD_API_KEY:
            logger.warning("CLOUD_API_KEY not set — falling back to local fallback model")
            return self.chat_local(messages, mode=mode, model=self.fallback_model)

        if not self.is_online():
            logger.warning("No internet — falling back to local fallback model")
            return self.chat_local(messages, mode=mode, model=self.fallback_model)

        system_prompt = self.SYSTEM_PROMPTS.get(mode, SYSTEM_PROMPT_GENERAL)

        headers = {
            "Authorization": f"Bearer {CLOUD_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/kuro-ai",
            "X-Title": "KURO AI Assistant",
        }

        payload = {
            "model": self.cloud_model,
            "messages": [{"role": "system", "content": system_prompt}] + messages,
            "temperature": 0.7,
            "max_tokens": 4096,
        }

        try:
            resp = requests.post(CLOUD_API_URL, headers=headers, json=payload, timeout=120)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.error(f"Cloud chat failed: {e} — falling back to local")
            return self.chat_local(messages, mode=mode, model=self.fallback_model)

    def stream_cloud(
        self,
        messages: list[dict],
        mode: str = "general",
    ) -> Iterator[str]:
        """
        Stream tokens from CLOUD model via OpenRouter.
        Falls back to local stream if offline.
        """
        if not CLOUD_API_KEY or not self.is_online():
            yield from self.stream_local(messages, mode=mode)
            return

        system_prompt = self.SYSTEM_PROMPTS.get(mode, SYSTEM_PROMPT_GENERAL)

        headers = {
            "Authorization": f"Bearer {CLOUD_API_KEY}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self.cloud_model,
            "messages": [{"role": "system", "content": system_prompt}] + messages,
            "stream": True,
            "temperature": 0.7,
            "max_tokens": 4096,
        }

        try:
            with requests.post(
                CLOUD_API_URL, headers=headers, json=payload,
                stream=True, timeout=120
            ) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if line and line.startswith(b"data: "):
                        data = line[6:]
                        if data == b"[DONE]":
                            break
                        chunk = json.loads(data)
                        token = chunk["choices"][0].get("delta", {}).get("content", "")
                        if token:
                            yield token
        except Exception as e:
            logger.error(f"Cloud stream failed: {e}")
            yield f"\n[Switching to local brain...]\n"
            yield from self.stream_local(messages, mode=mode)

    # ─── Unified Chat (auto-selects brain) ───────────────────────────────────
    def chat(
        self,
        messages: list[dict],
        use_cloud: bool = False,
        mode: str = "general",
    ) -> str:
        """
        Unified chat: automatically routes to LOCAL or CLOUD brain.
        use_cloud=True → CLOUD (with local fallback)
        use_cloud=False → LOCAL only
        """
        if use_cloud:
            return self.chat_cloud(messages, mode=mode)
        return self.chat_local(messages, mode=mode)

    def stream(
        self,
        messages: list[dict],
        use_cloud: bool = False,
        mode: str = "general",
    ) -> Iterator[str]:
        """Unified streaming: routes to LOCAL or CLOUD brain."""
        if use_cloud:
            yield from self.stream_cloud(messages, mode=mode)
        else:
            yield from self.stream_local(messages, mode=mode)

    # ─── Utility ─────────────────────────────────────────────────────────────
    def unload_model(self):
        """Force unload the local model from VRAM immediately."""
        try:
            requests.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={"model": self.local_model, "keep_alive": 0},
                timeout=10,
            )
            logger.info("Model unloaded from VRAM")
        except Exception as e:
            logger.warning(f"Could not unload model: {e}")

    def list_local_models(self) -> list[str]:
        """Return list of pulled Ollama models."""
        try:
            r = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
            models = r.json().get("models", [])
            return [m["name"] for m in models]
        except Exception:
            return []


# ═══════════════════════════════════════════════════════════════════════════════
# MODULE-LEVEL ASYNC UNIFIED ENTRY POINT
# All of Kuro's code calls complete() — never pings Groq or local directly.
# Auto-routes: Groq when online, local Ollama when offline.
# ═══════════════════════════════════════════════════════════════════════════════

import asyncio as _asyncio

# Keywords that signal complex tasks → Groq
COMPLEX_TASK_SIGNALS = [
    "code", "write", "generate", "create", "explain",
    "debug", "fix", "refactor", "algorithm", "function",
    "script", "program", "implement", "design", "analyze",
    "compare", "research", "summarize", "translate"
]

def _needs_groq(prompt: str) -> bool:
    """
    True if this task is complex enough to need Groq's full intelligence.
    Simple action commands can use Qwen local.
    """
    prompt_lower = prompt.lower()
    return any(word in prompt_lower for word in COMPLEX_TASK_SIGNALS)

async def complete(prompt: str, max_tokens: int = 256, force_backend: str = None) -> str:
    """
    Smart async LLM router.
      • Online  → Groq Llama-3.1-8b-instant (fast, smart)
      • Offline → Qwen-2.5-coder-3b (local, private)
    Automatically falls back to local if Groq throws an error.

    Args:
        prompt:     Text prompt / user message.
        max_tokens: Max tokens to generate.

    Returns:
        Response string.
    """
    from core.model_router import get_llm_backend, LLMBackend
    from core.llm_local import complete_local

    # Manual UI override takes priority
    backend = await get_llm_backend()

    # If in auto mode and this is a complex task, prefer Groq
    if force_backend is None and backend == LLMBackend.GROQ:
        use_groq = _needs_groq(prompt)
    elif force_backend == "groq":
        use_groq = True
    else:
        use_groq = False

    if use_groq:
        try:
            # Use GroqEngine via run_in_executor (it's sync)
            from core.llm_groq import GroqEngine
            _groq = GroqEngine()
            loop = _asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: _groq.chat(
                    messages=[{"role": "user", "content": prompt}],
                    mode="fast",
                )
            )
            return result
        except Exception as e:
            logger.warning(f"[LLMEngine] Groq failed ({e}), falling back to local Qwen")
            return await complete_local(prompt, max_tokens)
    else:
        try:
            return await complete_local(prompt, max_tokens)
        except Exception as e:
            logger.warning(f"[LLMEngine] Local failed ({e}), trying Groq")
            from core.llm_groq import GroqEngine
            _groq = GroqEngine()
            loop = _asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: _groq.chat(
                    messages=[{"role": "user", "content": prompt}],
                    mode="fast",
                )
            )
            return result

