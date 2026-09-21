"""
KURO - Main Brain Orchestrator
Coordinates: Intent → Route → Personality → RAG → Module → Response
"""
import warnings
warnings.warn(
    "core/brain.py is deprecated. Use kuro/pipeline.py instead. "
    "This module will be removed in Phase 6.",
    DeprecationWarning,
    stacklevel=2
)

import logging
import time
from typing import Iterator

from core.intent_classifier import classify
from core.model_router import route, Intent, RoutingDecision
from core.llm_groq import LLMEngineWithGroq as LLMEngine
from core.memory import Memory
from core.long_term_memory import KuroMemory
import threading
from core.memory_extractor import extract_and_save_facts
from kuro.personality.personality_modes import KuroPersonality, PersonalityMode
from kuro.personality.prompt_builder import build_system_prompt

logger = logging.getLogger(__name__)


class Brain:
    """
    Central coordinator for KURO.

    Flow:
      user_input
        → classify intent
        → routing decision (local vs cloud)
        → retrieve RAG context (if relevant)
        → delegate to appropriate module or LLM
        → return structured response
    """

    def __init__(self):
        self.llm    = LLMEngine()
        self.memory = Memory()
        self.ltm    = KuroMemory()
        self.personality = KuroPersonality()
        self._current_emotion = "neutral"

        # Lazy-loaded modules (only imported when needed to save RAM)
        self._rag        = None
        self._system     = None
        self._dsa_tutor  = None
        self._blender    = None

        logger.info("KURO Brain online — personality: %s", self.personality.mode_name())

    # ─── Lazy Module Loaders ────────────────────────────────────────────────
    @property
    def rag(self):
        if self._rag is None:
            from rag.retriever import RAGRetriever
            self._rag = RAGRetriever()
        return self._rag

    @property
    def system(self):
        if self._system is None:
            from system.commander import Commander
            self._system = Commander()
        return self._system

    @property
    def dsa_tutor(self):
        if self._dsa_tutor is None:
            from teaching.dsa_tutor import DSATutor
            self._dsa_tutor = DSATutor(self.llm)
        return self._dsa_tutor

    @property
    def blender(self):
        if self._blender is None:
            from creative.blender_engine import BlenderEngine
            self._blender = BlenderEngine(self.llm)
        return self._blender

    # ─── Main Entry Point ──────────────────────────────────────────────────
    def think(self, user_input: str) -> dict:
        """
        Process user input and return a structured response.

        Returns:
            {
                "response":       str,   — text to show user
                "intent":         str,   — classified intent
                "use_cloud":      bool,  — which brain was used
                "routing_reason": str,   — why that brain was chosen
                "mode":           str,   — system prompt mode
                "action":         str|None — system action taken (if any)
                "extras":         dict   — additional data (code, blender output, etc.)
            }
        """
        user_input = user_input.strip()
        if not user_input:
            return self._build_response("Say something — I'm listening.", Intent.GENERAL_CHAT)

        # Check for personality mode switches ("work mode", "chill mode", etc.)
        if self.personality.handle_voice_command(user_input):
            mode = self.personality.mode_name()
            return self._build_response(
                f"Switched to {mode} mode. Let's go.",
                Intent.GENERAL_CHAT
            )

        # Step 1: Classify intent
        intent = classify(user_input)
        logger.info(f"Intent: {intent.value}")

        # Step 2: Routing decision
        decision = route(user_input, intent, message_length=len(user_input))
        logger.info(f"Route: {'CLOUD' if decision.use_cloud else 'LOCAL'} — {decision.reason}")

        # Step 3: Save user turn to memory
        self.memory.add("user", user_input)
        self.memory.increment_interactions()

        # Step 4: Delegate to correct handler
        try:
            result = self._dispatch(user_input, intent, decision)
        except Exception as e:
            logger.error(f"Dispatch error: {e}")
            result = self._build_response(
                f"Something went wrong: {str(e)}",
                intent, decision
            )

        # Step 5: Save assistant response to memory
        self.memory.add("assistant", result["response"])
        
        # Step 6: Save conversation and extract key facts in the background
        self.ltm.save_conversation(user_input, result["response"])
        threading.Thread(
            target=extract_and_save_facts,
            args=(self.llm, self.ltm, user_input, result["response"]),
            daemon=True
        ).start()
        
        return result

    def stream(self, user_input: str) -> Iterator[str]:
        """
        Streaming version of think().
        Yields response tokens one by one.
        Also handles routing/memory as a side effect.
        """
        user_input = user_input.strip()
        intent = classify(user_input)
        decision = route(user_input, intent, message_length=len(user_input))

        self.memory.add("user", user_input)
        self.memory.increment_interactions()

        messages = self._build_messages(user_input, intent, decision)
        full_response = ""

        for token in self.llm.stream(messages, use_cloud=decision.use_cloud, mode=decision.mode):
            full_response += token
            yield token

        self.memory.add("assistant", full_response)
        
        # Save LTM
        self.ltm.save_conversation(user_input, full_response)
        threading.Thread(
            target=extract_and_save_facts,
            args=(self.llm, self.ltm, user_input, full_response),
            daemon=True
        ).start()

    # ─── Streaming Pipeline: LLM → Sentence → TTS ─────────────────────────
    def stream_and_speak(self, user_input: str, tts=None) -> dict:
        """
        Stream LLM tokens and speak each sentence as it completes.

        Instead of waiting for the full response, this method:
        1. Streams tokens from the LLM one at a time
        2. Buffers tokens into a sentence_buffer
        3. When a sentence terminator (. ! ? \\n) is hit, immediately fires
           tts.speak() (non-blocking) with that sentence
        4. Continues streaming — the LLM generates sentence N+1 while
           TTS speaks sentence N

        Returns the same dict as think() for compatibility.
        """
        user_input = user_input.strip()
        if not user_input:
            return self._build_response("Say something — I'm listening.", Intent.GENERAL_CHAT)

        # Personality mode switches (instant — no LLM needed)
        if self.personality.handle_voice_command(user_input):
            mode = self.personality.mode_name()
            msg = f"Switched to {mode} mode. Let's go."
            if tts:
                tts.speak(msg)
            return self._build_response(msg, Intent.GENERAL_CHAT)

        # Classify + route
        intent = classify(user_input)
        decision = route(user_input, intent, message_length=len(user_input))

        # Non-chat intents: fall back to regular think() (actions, system, etc.)
        if intent not in (Intent.GENERAL_CHAT, Intent.CODE_HELP, Intent.CODE_REVIEW, Intent.TEACH_DSA):
            return self.think(user_input)

        # Save user turn
        self.memory.add("user", user_input)
        self.memory.increment_interactions()

        # Build messages with personality + history + RAG
        messages = self._build_messages(user_input, intent, decision)

        # ── Stream tokens and speak per sentence ──────────────────────────
        t_start = time.perf_counter()
        full_response = ""
        sentence_buffer = ""
        sentence_count = 0
        first_sentence_spoken = False

        for token in self.llm.stream(messages, use_cloud=decision.use_cloud, mode=decision.mode):
            full_response += token
            sentence_buffer += token

            # Check if we have a complete sentence
            if any(p in token for p in [".", "!", "?", "\n"]):
                sentence = sentence_buffer.strip()
                if sentence and len(sentence) > 2:  # skip tiny fragments
                    sentence_count += 1
                    if tts:
                        tts.speak(sentence)  # non-blocking — fires in background thread
                    if not first_sentence_spoken:
                        t_first = time.perf_counter()
                        logger.info(
                            f"⏱ First sentence spoken at {t_first - t_start:.2f}s: "
                            f"'{sentence[:50]}...'"
                        )
                        first_sentence_spoken = True
                    sentence_buffer = ""

        # Flush any remaining buffer (partial sentence at end)
        remaining = sentence_buffer.strip()
        if remaining and tts:
            tts.speak(remaining)

        t_end = time.perf_counter()
        logger.info(
            f"⏱ stream_and_speak: {t_end - t_start:.2f}s total, "
            f"{sentence_count} sentences streamed"
        )

        # Save assistant response to memory
        self.memory.add("assistant", full_response)
        self.ltm.save_conversation(user_input, full_response)
        threading.Thread(
            target=extract_and_save_facts,
            args=(self.llm, self.ltm, user_input, full_response),
            daemon=True
        ).start()

        return self._build_response(full_response, intent, decision)

    # ─── Dispatch ──────────────────────────────────────────────────────────
    def _dispatch(self, user_input: str, intent: Intent, decision: RoutingDecision) -> dict:
        """Route to the correct module handler."""

        if intent == Intent.SYSTEM_COMMAND:
            return self._handle_system(user_input, intent, decision)

        if intent == Intent.SYSTEM_INFO:
            return self._handle_system_info(intent, decision)

        if intent in (Intent.BLENDER_CREATE, Intent.BLENDER_MODIFY):
            return self._handle_blender(user_input, intent, decision)

        if intent == Intent.TEACH_DSA:
            return self._handle_dsa(user_input, intent, decision)

        # Default: LLM chat with optional RAG context
        return self._handle_chat(user_input, intent, decision)

    # ─── Handlers (one per domain) ─────────────────────────────────────────
    def _handle_chat(self, user_input: str, intent: Intent, decision: RoutingDecision) -> dict:
        """General chat with optional RAG context."""
        messages = self._build_messages(user_input, intent, decision)
        response = self.llm.chat(messages, use_cloud=decision.use_cloud, mode=decision.mode)
        return self._build_response(response, intent, decision)

    def _handle_system(self, user_input: str, intent: Intent, decision: RoutingDecision) -> dict:
        """Execute a system command."""
        result = self.system.execute(user_input)
        return self._build_response(result["message"], intent, decision, action=result.get("action"))

    def _handle_system_info(self, intent: Intent, decision: RoutingDecision) -> dict:
        """Return system status info."""
        info = self.system.get_system_info()
        response = self._format_system_info(info)
        return self._build_response(response, intent, decision, action="system_info", extras=info)

    def _handle_dsa(self, user_input: str, intent: Intent, decision: RoutingDecision) -> dict:
        """Teach DSA topic using the cloud brain."""
        response = self.dsa_tutor.teach(user_input)
        return self._build_response(response, intent, decision)

    def _handle_blender(self, user_input: str, intent: Intent, decision: RoutingDecision) -> dict:
        """Generate and execute a Blender script."""
        result = self.blender.generate_and_run(user_input)
        return self._build_response(
            result["message"], intent, decision,
            action="blender_execute",
            extras={"script": result.get("script"), "output": result.get("output")},
        )

    # ─── Message Builder ──────────────────────────────────────────────────
    def _build_messages(self, user_input: str, intent: Intent, decision: RoutingDecision) -> list[dict]:
        """Build the messages list with personality system prompt + history + optional RAG context.
        Skips RAG/LTM for system commands to keep them instant.
        Uses capped context window to prevent slow models from choking on long history."""
        history = self.memory.get_context_window()[:-1]    # exclude the turn we just added; capped to CONTEXT_WINDOW_SIZE

        # Auto-update personality mode based on time of day (unless manually overridden)
        self.personality.auto_update(self._current_emotion)

        # Build system prompt with personality + emotion + memory context
        ltm_context = ""

        # Skip RAG + LTM for system/action commands — they don't need context
        if intent in (Intent.SYSTEM_COMMAND, Intent.SYSTEM_INFO):
            system_prompt = build_system_prompt(
                self.personality.current_mode, self._current_emotion
            )
            messages = [{"role": "system", "content": system_prompt}]
            messages.extend(history)
            messages.append({"role": "user", "content": user_input})
            return messages

        # Try to add RAG context for knowledge-heavy queries
        rag_context = ""
        if intent in (Intent.TEACH_DSA, Intent.CODE_HELP, Intent.CODE_REVIEW):
            try:
                docs = self.rag.retrieve(user_input, top_k=3)
                if docs:
                    rag_context = "\n\n--- RELEVANT CONTEXT ---\n"
                    for d in docs:
                        rag_context += f"\n{d['content']}\n"
                    rag_context += "--- END CONTEXT ---\n"
            except Exception as e:
                logger.debug(f"RAG retrieval skipped: {e}")

        ltm_context = self.ltm.get_context_for_prompt(user_input)
        if ltm_context:
            rag_context += f"\n{ltm_context}\n"

        # Build personality-aware system prompt with memory context
        system_prompt = build_system_prompt(
            self.personality.current_mode, self._current_emotion,
            memory_context=ltm_context
        )

        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        final_input = user_input + rag_context if rag_context else user_input
        messages.append({"role": "user", "content": final_input})
        return messages

    def set_emotion(self, emotion: str) -> None:
        """Called by the voice pipeline after emotion detection."""
        self._current_emotion = emotion
        self.personality.auto_update(emotion)
        logger.info(f"Emotion updated: {emotion} → mode: {self.personality.mode_name()}")

    # ─── Utilities ────────────────────────────────────────────────────────
    def _build_response(
        self,
        response: str,
        intent: Intent,
        decision: RoutingDecision | None = None,
        action: str | None = None,
        extras: dict | None = None,
    ) -> dict:
        return {
            "response": response,
            "intent": intent.value,
            "use_cloud": decision.use_cloud if decision else False,
            "routing_reason": decision.reason if decision else "direct",
            "mode": decision.mode if decision else "general",
            "action": action,
            "extras": extras or {},
        }

    def _format_system_info(self, info: dict) -> str:
        """Format system info dict into readable string."""
        lines = []
        if "cpu" in info:
            lines.append(f"CPU: {info['cpu'].get('usage_percent', 'N/A')}%")
        if "memory" in info:
            mem = info["memory"]
            lines.append(f"RAM: {mem.get('used_gb', 'N/A')}GB / {mem.get('total_gb', 'N/A')}GB")
        if "disk" in info:
            disk = info["disk"]
            lines.append(f"Disk: {disk.get('used_gb', 'N/A')}GB / {disk.get('total_gb', 'N/A')}GB")
        if "gpu" in info:
            lines.append(f"GPU: {info['gpu']}")
        return "\n".join(lines) if lines else "System info unavailable."
