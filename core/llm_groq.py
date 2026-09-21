"""
KURO — Groq LPU Engine
======================
Drop-in replacement for Ollama for chat/code tasks.

WHY GROQ:
  Groq built an LPU (Language Processing Unit) — hardware designed specifically
  for sequential token generation. GPUs are memory-bandwidth-bottlenecked for
  LLM inference (each token requires reading all weights from DRAM).
  Groq puts weights in massive on-chip SRAM → no memory bottleneck.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

# ── Model selection ────────────────────────────────────────────────────────────
# Fast model for commands/chat, smart model for code/complex reasoning
GROQ_FAST_MODEL  = "llama-3.1-8b-instant"       # ~800 tok/s — use for all voice commands
GROQ_SMART_MODEL = "llama-3.3-70b-versatile"    # ~300 tok/s — use for code gen, complex reasoning
GROQ_CODE_MODEL  = "mixtral-8x7b-32768"         # ~480 tok/s — use for code specifically


class GroqEngine:
    """
    Groq LPU inference engine.
    Provides the same interface as LLMEngine so it's a drop-in replacement.
    """

    def __init__(self):
        self._client = None
        self._available = False
        self._init()

    def _init(self) -> None:
        try:
            from groq import Groq
            api_key = os.getenv("GROQ_API_KEY", "")
            if not api_key:
                logger.warning(
                    "GROQ_API_KEY not set. Get a free key at https://console.groq.com\n"
                    "Then add to .env:  GROQ_API_KEY=gsk_xxxxxxxxxxxx"
                )
                return
            self._client = Groq(api_key=api_key)
            self._available = True
            logger.info(f"GroqEngine ready — fast={GROQ_FAST_MODEL}, smart={GROQ_SMART_MODEL}")
        except ImportError:
            logger.warning("groq not installed — run: pip install groq")
        except Exception as e:
            logger.warning(f"GroqEngine init failed: {e}")

    @property
    def available(self) -> bool:
        return self._available

    def _pick_model(self, mode: str) -> str:
        if mode in ("code", "coding", "blender", "dsa"):
            return GROQ_CODE_MODEL
        if mode in ("smart", "complex", "cloud"):
            return GROQ_SMART_MODEL
        return GROQ_FAST_MODEL

    def chat(self, messages: list[dict], mode: str = "fast") -> str:
        if not self._available:
            raise RuntimeError("Groq not available — check GROQ_API_KEY")

        model = self._pick_model(mode)
        t0 = time.perf_counter()

        try:
            response = self._client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=512,
                temperature=0.4,
                stream=False,
            )
            text = response.choices[0].message.content or ""
            elapsed = time.perf_counter() - t0
            tokens = response.usage.completion_tokens if response.usage else 0
            logger.info(f"Groq [{model}]: {tokens} tokens in {elapsed:.2f}s ({tokens/elapsed:.0f} tok/s)")
            return text.strip()

        except Exception as e:
            logger.error(f"Groq chat failed: {e}")
            raise

    def stream(self, messages: list[dict], mode: str = "fast") -> Iterator[str]:
        if not self._available:
            raise RuntimeError("Groq not available — check GROQ_API_KEY")

        model = self._pick_model(mode)

        try:
            stream = self._client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=512,
                temperature=0.4,
                stream=True,
            )
            for chunk in stream:
                token = chunk.choices[0].delta.content
                if token:
                    yield token
        except Exception as e:
            logger.error(f"Groq stream failed: {e}")
            raise

    def stream_code(self, messages: list[dict], max_tokens: int = 1500) -> Iterator[str]:
        if not self._available:
            raise RuntimeError("Groq not available")

        try:
            stream = self._client.chat.completions.create(
                model=GROQ_CODE_MODEL,
                messages=messages,
                max_tokens=max_tokens,
                temperature=0.2,
                stream=True,
            )
            for chunk in stream:
                token = chunk.choices[0].delta.content
                if token:
                    yield token
        except Exception as e:
            logger.error(f"Groq stream_code failed: {e}")
            raise


class LLMEngineWithGroq:
    """
    Drop-in replacement for core/llm_engine.py LLMEngine.
    Tries Groq first (fast), falls back to Ollama if Groq unavailable.
    """

    def __init__(self):
        self.groq = GroqEngine()
        self._ollama: Optional[object] = None

    def _get_ollama(self):
        if self._ollama is None:
            from core.llm_engine import LLMEngine
            self._ollama = LLMEngine()
        return self._ollama

    def _mode_needs_groq(self, mode: str, use_cloud: bool) -> bool:
        if not self.groq.available:
            return False
        if use_cloud:
            return True
        if mode in ("code", "coding", "dsa", "blender", "smart"):
            return True
        return True

    def chat(self, messages: list[dict], use_cloud: bool = False, mode: str = "general") -> str:
        if self._mode_needs_groq(mode, use_cloud):
            try:
                return self.groq.chat(messages, mode=mode)
            except Exception as e:
                logger.error(f"Groq failed ({e}) — check GROQ_API_KEY environment variable")
                raise  # Don't fallback to Ollama — fail loudly for diagnostics

        raise RuntimeError("LLMEngineWithGroq: only Groq is supported in KuroPipeline")

    def stream(self, messages: list[dict], use_cloud: bool = False, mode: str = "general") -> Iterator[str]:
        if self._mode_needs_groq(mode, use_cloud):
            try:
                yield from self.groq.stream(messages, mode=mode)
                return
            except Exception as e:
                logger.warning(f"Groq stream failed ({e}), falling back to Ollama")

        yield from self._get_ollama().stream(messages, use_cloud=False, mode=mode)

    def chat_local(self, messages: list[dict], mode: str = "general", model=None) -> str:
        return self.chat(messages, use_cloud=False, mode=mode)

    def stream_local(self, messages: list[dict], mode: str = "general") -> Iterator[str]:
        yield from self.stream(messages, use_cloud=False, mode=mode)

    def chat_cloud(self, messages: list[dict], mode: str = "general") -> str:
        if self.groq.available:
            try:
                return self.groq.chat(messages, mode="smart")
            except Exception as e:
                logger.warning(f"Groq cloud failed: {e}")
        return self._get_ollama().chat_cloud(messages, mode=mode)
