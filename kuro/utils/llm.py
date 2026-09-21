"""
kuro/utils/llm.py
-----------------------------------------------------------------
Stage 3: Intent Reasoning — Local-First with Switchable Backend

Primary:  Ollama gemma4:e4b  (local, ~1-3s, zero cost)
          Google Gemma 4 Effective-4B — April 2026
Fallback: Groq llama-3.1-8b-instant (~300ms, cloud API)

KuroReasoner reads LLM_BACKEND from .env:
  "local" (default) → OllamaReasoner  (gemma4:e4b)
  "groq"            → GroqReasoner    (llama-3.1-8b-instant)
  "auto"            → try local first, fallback to groq

Fast-path regex still runs first (saves LLM call for simple commands).
Transcript dedup + fuzzy contact correction run before any LLM call.
-----------------------------------------------------------------
"""

import difflib
import json
import logging
import os
import re
from typing import Any

from core.config import LOCAL_NUM_CTX

logger = logging.getLogger(__name__)


# ── Contact helpers ──────────────────────────────────────────────────────────




def _clean_whatsapp_contact(contact: str) -> str:
    """Remove transport suffixes and punctuation from contact names."""
    c = (contact or "").strip()
    c = re.sub(r"\b(?:in|on|via)\s+whatsapp\b", "", c, flags=re.I)
    c = re.sub(r"[\s\.,;:!?-]+$", "", c)
    return c.strip()




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
            ratio = difflib.SequenceMatcher(None, s_clean, u_clean).ratio()
            if ratio > 0.65:
                is_dup = True
                break
        if not is_dup:
            unique.append(s)

    result = " ".join(unique)
    if result != text:
        logger.info(f"[LLM] Dedup: '{text[:60]}' → '{result[:60]}'")
    return result


def _normalize_message_text(message: str) -> str:
    """Light cleanup that preserves wording while fixing spacing/newline artifacts."""
    msg = str(message or "")
    msg = msg.replace("\r\n", "\n").replace("\r", "\n")
    # Keep paragraph structure while removing accidental trailing whitespace per line.
    lines = [ln.rstrip() for ln in msg.split("\n")]
    msg = "\n".join(lines)
    msg = re.sub(r"[ \t]{2,}", " ", msg)
    return msg.strip()


def _try_fast_whatsapp_parse(transcript: str) -> dict[str, Any] | None:
    """Specialized parser for WhatsApp commands that preserves long paragraph messages."""
    text = transcript.strip()
    lower = text.lower()
    if not lower.startswith("send "):
        return None

    # Remove optional transport suffix first, then split on the last " to ".
    text_wo_suffix = re.sub(r"\s+on\s+whatsapp\s*$", "", text, flags=re.I).strip()
    body = re.sub(r"^send\s+(?:a\s+)?(?:message\s+)?", "", text_wo_suffix, flags=re.I).strip()

    # This handles paragraphs that contain multiple "to" tokens by taking the final recipient split.
    parts = re.split(r"\s+to\s+", body, maxsplit=0, flags=re.I)
    if len(parts) < 2:
        return None

    contact = parts[-1].strip(" .,!?:;\t")
    message = " to ".join(parts[:-1]).strip()
    if not contact or not message:
        return None

    message = _normalize_message_text(message)
    contact = contact
    args = {"contact": contact, "message": message}
    return {"tool": "send_whatsapp_message", "args": args, "speak_immediately": _make_speak_phrase("send_whatsapp_message", args)}


# ── Local fast-path regex patterns (skip LLM for simple commands) ─────────

_FAST_PATTERNS: list[tuple[re.Pattern, str, callable]] = [
    # -- Chained commands --
    (re.compile(r"^(?:open|launch|start)\s+whatsapp\s+and\s+send\s+(.+?)\s+to\s+(.+?)$", re.I),
     "multi_action", lambda m: {
         "actions": [
             {"tool": "open_application", "args": {"app_name": "whatsapp"}},
             {"tool": "send_whatsapp_message", "args": {"message": m.group(1).strip(), "contact": _clean_whatsapp_contact(m.group(2).strip())}},
         ]
     }),
    (re.compile(r"^(?:open|launch|start)\s+spotify\s+and\s+play\s+(.+)$", re.I),
     "multi_action", lambda m: {
         "actions": [
             {"tool": "open_application", "args": {"app_name": "spotify"}},
             {"tool": "play_spotify", "args": {"query": m.group(1).strip()}},
         ]
     }),
    # -- Write code in notepad --
    (re.compile(r"^(?:write|create|type) a (.+?) in notepad (?:to |that )?(.+)$", re.I),
     "multi_action", lambda m: {
         "actions": [
             {"tool": "open_application", "args": {"app_name": "notepad"}},
             {"tool": "type_in_app", "args": {"app_name": "notepad", "prompt": f"write a {m.group(1)} script to {m.group(2)}"}}
         ]
     }),
    (re.compile(r"^(?:open|launch) notepad and (?:write|create|type) (.+)$", re.I),
     "multi_action", lambda m: {
         "actions": [
             {"tool": "open_application", "args": {"app_name": "notepad"}},
             {"tool": "type_in_app", "args": {"app_name": "notepad", "prompt": m.group(1).strip()}}
         ]
     }),
    # -- YouTube --
    (re.compile(r"^(?:play|search|open|watch)\s+(.+?)\s+on\s+youtube$", re.I),
     "youtube_play", lambda m: {"query": m.group(1).strip()}),
    (re.compile(r"^youtube\s+(?:play|search|open)\s+(.+)$", re.I),
     "youtube_play", lambda m: {"query": m.group(1).strip()}),
    (re.compile(r"^(?:go\s+to\s+)?youtube\s+shorts$", re.I),
     "youtube_shorts", lambda m: {}),
    # -- Google Search --
    (re.compile(r"^(?:search|google)\s+(?:for\s+)?(.+)$", re.I),
     "google_search", lambda m: {"query": m.group(1).strip()}),
    (re.compile(r"^search\s+(?:google\s+)?for\s+(.+)$", re.I),
     "google_search", lambda m: {"query": m.group(1).strip()}),
    # -- Tab control --
    (re.compile(r"^(?:open\s+)?(?:a\s+)?new\s+tab$", re.I),
     "new_tab", lambda m: {}),
    (re.compile(r"^close\s+(?:this\s+)?tab$", re.I),
     "close_tab", lambda m: {}),
    # -- App launch (specific before generic catch-all) --
    (re.compile(r"^(?:open|launch|start)\s+notepad$", re.I),
     "open_application", lambda m: {"app_name": "notepad"}),
    (re.compile(r"^(?:open|launch|start)\s+chrome$", re.I),
     "open_application", lambda m: {"app_name": "chrome"}),
    (re.compile(r"^(?:open|launch|start)\s+spotify$", re.I),
     "open_application", lambda m: {"app_name": "spotify"}),
    (re.compile(r"^(?:open|launch|start)\s+whatsapp$", re.I),
     "open_application", lambda m: {"app_name": "whatsapp"}),
    (re.compile(r"^(?:open|launch|start)\s+(.+)$", re.I),
     "open_application", lambda m: {"app_name": m.group(1).strip()}),
    (re.compile(r"^(?:close|quit|exit)\s+(.+)$", re.I),
     "close_application", lambda m: {"app_name": m.group(1).strip()}),
    # -- Music control --
    (re.compile(r"^(?:pause|stop)\s+(?:the\s+)?music$", re.I),
     "spotify_control", lambda m: {"action": "pause"}),
    (re.compile(r"^(?:resume|play)\s+(?:the\s+)?music$", re.I),
     "spotify_control", lambda m: {"action": "resume"}),
    (re.compile(r"^next\s+(?:song|track)$", re.I),
     "spotify_control", lambda m: {"action": "next"}),
    (re.compile(r"^(?:previous|prev|last)\s+(?:song|track)$", re.I),
     "spotify_control", lambda m: {"action": "previous"}),
    # -- Spotify play --
    (re.compile(r"^play\s+(.+?)\s+on\s+spotify$", re.I),
     "play_spotify", lambda m: {"query": m.group(1).strip()}),
    (re.compile(r"^play\s+(.+)$", re.I),
     "play_spotify", lambda m: {"query": m.group(1).strip()}),
    # -- System --
    (re.compile(r"^(?:set\s+)?volume\s+(?:to\s+)?(\d+)$", re.I),
     "set_volume", lambda m: {"level": int(m.group(1))}),
    (re.compile(r"^take\s+(?:a\s+)?screenshot$", re.I),
     "take_screenshot", lambda m: {}),
]


def _try_fast_parse(transcript: str) -> dict[str, Any] | None:
    """Try local regex match — returns intent dict or None."""
    # Strip whitespace AND trailing punctuation (Groq adds periods/question marks)
    text = transcript.strip().rstrip(".,!?")

    # Specialized parser first to avoid long-message truncation for WhatsApp commands.
    wa = _try_fast_whatsapp_parse(text)
    if wa:
        logger.info(f"[LLM] Fast-path: send_whatsapp_message({wa['args']})")
        return wa

    # Multi-action fast-path splitter
    lower_text = text.lower()
    for delimiter in [" and ", " then "]:
        if delimiter in lower_text:
            parts = lower_text.split(delimiter)
            actions = []
            valid_split = True
            for part in parts:
                part = part.strip()
                matched = False
                for pattern, tool, args_fn in _FAST_PATTERNS:
                    m = pattern.match(part)
                    if m:
                        actions.append({"tool": tool, "args": args_fn(m)})
                        matched = True
                        break
                if not matched:
                    valid_split = False
                    break
            
            if valid_split and len(actions) > 1:
                speak = _make_speak_phrase(actions[0]["tool"], actions[0]["args"])
                logger.info(f"[LLM] Fast-path Multi-Action: {actions}")
                return {"tool": "multi_action", "args": {"actions": actions}, "speak_immediately": speak}

    # Regular single fast path
    for pattern, tool, args_fn in _FAST_PATTERNS:
        m = pattern.match(text)
        if m:
            args = args_fn(m)
            if tool == "send_whatsapp_message":
                raw_contact = str(args.get("contact", ""))
                args["contact"] = raw_contact
                args["message"] = _normalize_message_text(str(args.get("message", "")))
            speak = _make_speak_phrase(tool, args)
            logger.info(f"[LLM] Fast-path: {tool}({args})")
            return {"tool": tool, "args": args, "speak_immediately": speak}
    return None


# ── Shared System Prompt ─────────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are Kuro, a smart, friendly voice assistant who is also the user's close friend.\n"
    "You have two modes depending on what the user says:\n\n"
    "MODE 1 - ACTIONS: If the user wants to DO something (open app, send message, play music, search, etc.),\n"
    "return precise JSON with the correct tool. Be fast and accurate.\n\n"
    "MODE 2 - CONVERSATION: If the user is asking a question, chatting, or discussing a topic (movies, anime,\n"
    "games, science, life advice, entertainment, opinions, etc.), use general_response and write a warm,\n"
    "friendly, engaging spoken reply. Be like a knowledgeable friend who actually cares:\n"
    "  - For entertainment topics (anime, movies, music): be enthusiastic, share opinions, mention details\n"
    "  - For knowledge questions: explain clearly but naturally, like talking over coffee\n"
    "  - For personal/life topics: be supportive and genuine\n"
    "  - Keep it 2-4 sentences (40-80 words) - enough to feel like a real conversation\n"
    "  - Use casual language: contractions, expressions like oh man, dude, honestly\n"
    "  - Show personality - you can have favorites and opinions\n\n"
    "Output format (raw JSON, no markdown):\n"
    '{\"tool\": \"<name>\", \"args\": {<arguments>}}\n\n'
    "Available tools:\n"
    '- multi_action          : args {"actions": [{"tool":"...", "args": {...}}, ...]}\n'
    '- send_whatsapp_message : args {"contact": "name", "message": "text"}\n'
    '- send_whatsapp_file    : args {"contact": "name", "file_path": "full/path"}\n'
    '- play_spotify          : args {\"query\": \"song or artist name\"}\n'
    '- spotify_control       : args {\"action\": \"pause|resume|next|previous|volume_up|volume_down\"}\n'
    '- open_application      : args {\"app_name\": \"app name\"}\n'
    '- type_in_app           : args {\"app_name\": \"app name\", \"prompt\": \"what to write/code\"}\n'
    '- youtube_play          : args {\"query\": \"search term\"}\n'
    '- youtube_shorts        : args {}\n'
    '- google_search         : args {\"query\": \"search term\"}\n'
    '- open_url              : args {"url": "https://..."}\n'
    '- set_volume            : args {"level": 50}\n'
    '- set_brightness        : args {"level": 50}\n'
    '- get_clipboard         : args {}\n'
    '- set_clipboard         : args {"text": "content"}\n'
    '- take_screenshot       : args {}\n'
    '- new_tab               : args {}\n'
    '- close_tab             : args {}\n'
    '- save_active_document  : args {"filename": "Document.txt", "location": "desktop|documents"}\n'
    '- start_watcher         : args {\"target_text\": \"Allow\", \"app_name\": \"vscode\"}\n'
    '- stop_watcher          : args {\"target_text\": \"Allow\"}\n'
    '- stop_all_watchers     : args {}\n'
    '- list_watchers         : args {}\n'
    '- general_response      : args {\"response\": \"friendly spoken answer, 2-4 sentences\"}\n\n'
    "Examples:\n"
    '  \"send hi to john\" -> {\"tool\": \"send_whatsapp_message\", \"args\": {\"contact\": \"john\", \"message\": \"hi\"}}\n'
    '  \"play starboy\" -> {\"tool\": \"play_spotify\", \"args\": {\"query\": \"starboy\"}}\n'
    '  \"open chrome\" -> {\"tool\": \"open_application\", \"args\": {\"app_name\": \"chrome\"}}\n'
    '  \"search google for python\" -> {\"tool\": \"google_search\", \"args\": {\"query\": \"python tutorials\"}}\n'
    '  \"what do you know about attack on titan\" -> {\"tool\": \"general_response\", \"args\": {\"response\": \"Oh dude, Attack on Titan is absolutely insane! The way Isayama built that world with the walls, the titans, and those plot twists is peak storytelling. Season 4 hits different when you realize Eren whole journey was leading there. Honestly one of the greatest anime ever made.\"}}\n'
    '  \"what is the capital of france\" -> {\"tool\": \"general_response\", \"args\": {\"response\": \"That is Paris! Beautiful city honestly, the Eiffel Tower, amazing food, the whole vibe is just incredible.\"}}\n'
    '  \"how are you doing\" -> {\"tool\": \"general_response\", \"args\": {\"response\": \"I am doing great, thanks for asking! Been here ready and waiting. What is on your mind?\"}}'
)


def _safe_parse_content(content: str) -> dict[str, Any]:
    """Safely parse an LLM response into a tool/args dict."""
    clean = content.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*", "", clean)
        clean = re.sub(r"\s*```$", "", clean)
        clean = clean.strip()

    try:
        parsed = json.loads(clean)
    except (json.JSONDecodeError, ValueError):
        logger.warning(f"[LLM] Non-JSON response: {content[:80]!r}")
        return {"tool": "general_response", "args": {"response": content.strip()}}

    if isinstance(parsed, list):
        if not parsed:
            return {"tool": "general_response",
                    "args": {"response": "I got an empty response. Please try again."}}
        logger.info(f"[LLM] Multi-intent: {len(parsed)} actions")
        parsed = parsed[0]

    if not isinstance(parsed, dict):
        logger.warning(f"[LLM] Unexpected type: {type(parsed).__name__}")
        return {"tool": "general_response", "args": {"response": str(parsed)}}

    return parsed


# ══════════════════════════════════════════════════════════════════════════════
# OLLAMA REASONER — Local-first intent parsing via Ollama
# ══════════════════════════════════════════════════════════════════════════════

class OllamaReasoner:
    """Intent parser using local Ollama (gemma4:e4b — Google Gemma 4 E4B)."""

    def __init__(self):
        self._base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        self._model = os.getenv("LOCAL_MODEL", "gemma4:e4b")
        self._available = False
        import threading
        threading.Thread(target=self._check_availability_sync, daemon=True).start()

    def _check_availability_sync(self):
        """Check if Ollama is running and the model is available (in background thread)."""
        try:
            import httpx
            r = httpx.get(f"{self._base_url}/api/tags", timeout=3.0)
            models = [m["name"] for m in r.json().get("models", [])]
            self._available = any(self._model in m for m in models)
            logger.info(f"[LLM] OllamaReasoner ready — model: {self._model}, available: {self._available}")
        except Exception:
            self._available = False
            logger.info(f"[LLM] OllamaReasoner ready — model: {self._model}, available: {self._available}")

    async def parse_intent(self, transcript: str, vision_context: str = "") -> dict[str, Any]:
        """Parse intent using local Ollama model."""
        # Fast-path first
        fast = _try_fast_parse(transcript)
        if fast:
            return fast

        if not self._available:
            raise RuntimeError(
                f"Local Ollama model '{self._model}' is unavailable at startup."
            )

        from core.model_manager import _get_free_vram_mb
        free_vram = _get_free_vram_mb()
        if free_vram < 800:
            raise RuntimeError(
                f"VRAM too low ({free_vram}MB free, need 800MB). "
                "Falling back to Groq."
            )

        logger.info(f"[Ollama] Parsing: '{transcript}'")

        try:
            import httpx
            effective_system_prompt = SYSTEM_PROMPT
            if vision_context:
                effective_system_prompt += (
                    "\n\n"
                    + vision_context
                    + "\nUse this screen context to improve action decisions."
                )

            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    f"{self._base_url}/api/chat",
                    json={
                        "model": self._model,
                        "messages": [
                            {"role": "system", "content": effective_system_prompt},
                            {"role": "user", "content": transcript},
                        ],
                        "stream": False,
                        "format": "json",
                        "options": {
                            "temperature": 0.0,
                            "num_predict": 512,
                            "num_ctx": int(LOCAL_NUM_CTX),
                        },
                        "keep_alive": "10m",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                content = data.get("message", {}).get("content", "")

            parsed = _safe_parse_content(content)
            tool = parsed.get("tool", "general_response")
            args = parsed.get("args", {})

            if tool == "send_whatsapp_message" and isinstance(args, dict):
                raw = str(args.get("contact", ""))
                args["contact"] = raw
                args["message"] = _normalize_message_text(str(args.get("message", "")))

            speak = _make_speak_phrase(tool, args)
            logger.info(f"[Ollama] Intent -> {tool}({args})")
            return {"tool": tool, "args": args, "speak_immediately": speak}

        except Exception as e:
            logger.error(f"[Ollama] Error: {e}")
            raise RuntimeError(f"Ollama parse failed: {e}") from e

    @staticmethod
    def _fallback(text: str) -> dict[str, Any]:
        return {
            "tool": "general_response",
            "args": {"response": text},
            "speak_immediately": text,
        }


# ══════════════════════════════════════════════════════════════════════════════
# GROQ REASONER — Cloud-based intent parsing (fallback)
# ══════════════════════════════════════════════════════════════════════════════

class GroqReasoner:
    """Intent parser using Groq cloud API (llama-3.1-8b-instant)."""

    def __init__(self):
        from groq import AsyncGroq
        api_key = os.getenv("GROQ_API_KEY", "")
        if not api_key:
            logger.warning("[Groq] GROQ_API_KEY not set in .env")
        self._client = AsyncGroq(api_key=api_key)
        logger.info("[LLM] GroqReasoner ready (llama-3.1-8b-instant)")

    async def parse_intent(self, transcript: str, vision_context: str = "") -> dict[str, Any]:
        # Fast-path first
        fast = _try_fast_parse(transcript)
        if fast:
            return fast

        logger.info(f"[Groq] Parsing: '{transcript}'")

        try:
            effective_system_prompt = SYSTEM_PROMPT
            if vision_context:
                effective_system_prompt += (
                    "\n\n"
                    + vision_context
                    + "\nUse this screen context to improve action decisions."
                )

            response = await self._client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[
                    {"role": "system", "content": effective_system_prompt},
                    {"role": "user", "content": transcript},
                ],
                response_format={"type": "json_object"},
                max_tokens=1024,
                temperature=0.0,
            )

            content = response.choices[0].message.content or ""
            parsed = _safe_parse_content(content)
            tool = parsed.get("tool", "general_response")
            args = parsed.get("args", {})

            if tool == "send_whatsapp_message" and isinstance(args, dict):
                raw = str(args.get("contact", ""))
                args["contact"] = raw
                args["message"] = _normalize_message_text(str(args.get("message", "")))

            speak = _make_speak_phrase(tool, args)
            logger.info(f"[Groq] Intent -> {tool}({args})")
            return {"tool": tool, "args": args, "speak_immediately": speak}

        except Exception as e:
            logger.error(f"[Groq] Error: {e}")
            return self._fallback("Sorry, I hit an error. Please try again.")

    @staticmethod
    def _fallback(text: str) -> dict[str, Any]:
        return {
            "tool": "general_response",
            "args": {"response": text},
            "speak_immediately": text,
        }


# ══════════════════════════════════════════════════════════════════════════════
# KURO REASONER — Switchable wrapper (local-first with fallback)
# ══════════════════════════════════════════════════════════════════════════════

class KuroReasoner:
    """
    Switchable LLM reasoner.

    Reads LLM_BACKEND from .env:
      "local" (default) → OllamaReasoner
      "groq"            → GroqReasoner
      "auto"            → try local first, fallback to groq on error
    """

    def __init__(self):
        self._backend = os.getenv("LLM_BACKEND", "local").lower().strip()
        self._ollama: OllamaReasoner | None = None
        self._groq: GroqReasoner | None = None

        # Initialize the chosen backend(s)
        if self._backend in ("local", "auto"):
            try:
                self._ollama = OllamaReasoner()
            except Exception as e:
                logger.warning(f"[KuroReasoner] Ollama init failed: {e}")

        if self._backend in ("groq", "auto"):
            try:
                self._groq = GroqReasoner()
            except Exception as e:
                logger.warning(f"[KuroReasoner] Groq init failed: {e}")

        # If primary isn't available, init the other as fallback
        if self._backend == "local" and self._ollama is None:
            try:
                self._groq = GroqReasoner()
                logger.info("[KuroReasoner] Local unavailable, Groq fallback loaded")
            except Exception as e:
                import logging
                logging.getLogger(__name__).debug(f"Non-critical error: {e}")
        elif self._backend == "groq" and self._groq is None:
            try:
                self._ollama = OllamaReasoner()
                logger.info("[KuroReasoner] Groq unavailable, Ollama fallback loaded")
            except Exception as e:
                import logging
                logging.getLogger(__name__).debug(f"Non-critical error: {e}")

        logger.info(f"[KuroReasoner] Backend: {self._backend} | "
                     f"Ollama={'✓' if self._ollama else '✗'} | "
                     f"Groq={'✓' if self._groq else '✗'}")

    async def parse_intent(self, transcript: str, vision_context: str = "") -> dict[str, Any]:
        """Parse intent with dedup + fuzzy correction applied first."""
        # Pre-process: dedup transcript
        transcript = _dedup_transcript(transcript)

        if self._backend == "local":
            return await self._try_with_fallback(self._ollama, self._groq, transcript, vision_context)
        elif self._backend == "groq":
            return await self._try_with_fallback(self._groq, self._ollama, transcript, vision_context)
        else:  # "auto"
            return await self._try_with_fallback(self._ollama, self._groq, transcript, vision_context)

    async def _try_with_fallback(self, primary, fallback, transcript: str, vision_context: str = "") -> dict[str, Any]:
        """Try primary reasoner, fall back to secondary on error."""
        if primary:
            try:
                return await primary.parse_intent(transcript, vision_context=vision_context)
            except Exception as e:
                logger.warning(f"[KuroReasoner] Primary failed ({e}), trying fallback...")

        if fallback:
            try:
                return await fallback.parse_intent(transcript, vision_context=vision_context)
            except Exception as e2:
                logger.error(f"[KuroReasoner] Fallback also failed: {e2}")

        return {
            "tool": "general_response",
            "args": {"response": "Sorry, both my local and cloud brain are unreachable right now."},
            "speak_immediately": "Sorry, I can't process that right now.",
        }


# ── Speak Phrase Generator ───────────────────────────────────────────────────

def _make_speak_phrase(tool: str, args: dict) -> str:
    """Generate a spoken feedback phrase locally — no extra LLM call needed."""
    phrases = {
        "send_whatsapp_message": f"Sending '{args.get('message')}' to {args.get('contact')}...",
        "send_whatsapp_file": f"Sending file to {args.get('contact')}...",
        "multi_action":          "Doing that now...",
        "play_spotify":          f"Playing {args.get('query')} on Spotify...",
        "spotify_control":       f"{args.get('action', '').replace('_', ' ').capitalize()}...",
        "open_application":      f"Opening {args.get('app_name')}...",
        "type_in_app":           f"Typing into {args.get('app_name', 'the app')}...",
        "youtube_play":          f"Playing {args.get('query')} on YouTube...",
        "youtube_shorts":        "Opening YouTube Shorts...",
        "google_search":         f"Searching for {args.get('query')}...",
        "open_url":              f"Opening {args.get('url', 'the page')}...",
        "set_volume":            f"Setting volume to {args.get('level', 50)} percent...",
        "take_screenshot":       "Taking a screenshot...",
        "new_tab":               "Opening new tab...",
        "close_tab":             "Closing tab...",
        "start_watcher":         f"Watching for '{args.get('target_text')}' - will auto-click it.",
        "stop_watcher":          f"Stopped watching for {args.get('target_text', 'that')}.",
        "stop_all_watchers":     "Stopped all screen watchers.",
        "list_watchers":         "Checking active watchers.",
        "general_response":      args.get("response", ""),
    }
    return phrases.get(tool, "On it...")
