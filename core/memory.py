"""
KURO - Memory System
Handles short-term (conversation), long-term (ChromaDB), and episodic (JSON) memory
"""
import warnings
warnings.warn(
    "core/memory.py is deprecated. Use kuro/pipeline.py instead. "
    "This module will be removed in Phase 6.",
    DeprecationWarning,
    stacklevel=2
)

import json
import logging
from datetime import datetime
from pathlib import Path

from core.config import DATA_DIR, MAX_HISTORY

logger = logging.getLogger(__name__)


class Memory:
    """
    Three-layer memory:
      - Short-term : in-memory list (last N messages)
      - Episodic   : daily JSON log (timestamps, full turns)
      - Profile    : persistent user preferences JSON
    """

    def __init__(self):
        self.history: list[dict] = []   # [{role: "user"|"assistant", content: "..."}]
        self.history_dir = DATA_DIR / "conversation_history"
        self.profile_file = DATA_DIR / "user_profile.json"
        self.history_dir.mkdir(parents=True, exist_ok=True)

        self.profile = self._load_profile()
        logger.info("Memory system initialized")

    # ─── Short-Term Memory ──────────────────────────────────────────────────
    def add(self, role: str, content: str) -> None:
        """Add a message to short-term memory. Trims oldest if > MAX_HISTORY."""
        self.history.append({"role": role, "content": content})
        if len(self.history) > MAX_HISTORY:
            # Remove oldest pair (user + assistant)
            self.history = self.history[-MAX_HISTORY:]
        self._log_episode(role, content)

    def get_history(self) -> list[dict]:
        """Return current short-term conversation history."""
        return self.history.copy()

    def clear(self) -> None:
        """Clear short-term memory (start fresh conversation)."""
        self.history = []
        logger.info("Short-term memory cleared")

    def last_n(self, n: int = 4) -> list[dict]:
        """Return last N messages for compact context."""
        return self.history[-n:] if len(self.history) >= n else self.history.copy()

    def get_context_window(self) -> list[dict]:
        """
        Return a capped context window for the LLM.
        Uses CONTEXT_WINDOW_SIZE from config to prevent sending too many
        messages, which causes progressive slowdown on small models.
        """
        try:
            from core.config import CONTEXT_WINDOW_SIZE
        except ImportError:
            CONTEXT_WINDOW_SIZE = 10
        if len(self.history) <= CONTEXT_WINDOW_SIZE:
            return self.history.copy()
        return self.history[-CONTEXT_WINDOW_SIZE:]

    def summarize_and_compact(self, llm=None) -> None:
        """
        Summarize old messages into a single 'system' message and compact history.
        Called periodically (every SUMMARIZE_EVERY_N messages) to prevent context bloat.
        If no LLM is provided, just truncates to CONTEXT_WINDOW_SIZE.
        """
        try:
            from core.config import CONTEXT_WINDOW_SIZE, SUMMARIZE_EVERY_N
        except ImportError:
            CONTEXT_WINDOW_SIZE, SUMMARIZE_EVERY_N = 10, 20

        if len(self.history) < SUMMARIZE_EVERY_N:
            return  # not enough messages to warrant compaction

        if llm is None:
            # No LLM available — just truncate
            self.history = self.history[-CONTEXT_WINDOW_SIZE:]
            logger.info(f"Memory compacted (truncated to {CONTEXT_WINDOW_SIZE} messages)")
            return

        # Use LLM to summarize the old messages
        old_messages = self.history[:-CONTEXT_WINDOW_SIZE]
        recent_messages = self.history[-CONTEXT_WINDOW_SIZE:]

        conversation_text = "\n".join(
            f"{m['role'].upper()}: {m['content'][:200]}" for m in old_messages
        )

        try:
            summary = llm.chat_local(
                [{"role": "user", "content": (
                    f"Summarize this conversation in 2-3 sentences. "
                    f"Include key facts and user preferences:\n\n{conversation_text}"
                )}],
                mode="fast",
            )
            # Prepend summary as a system-context message
            summary_msg = {
                "role": "system",
                "content": f"[Previous conversation summary: {summary}]"
            }
            self.history = [summary_msg] + recent_messages
            logger.info(f"Memory compacted: {len(old_messages)} messages → 1 summary + {len(recent_messages)} recent")
        except Exception as e:
            logger.warning(f"Memory summarization failed: {e} — truncating instead")
            self.history = self.history[-CONTEXT_WINDOW_SIZE:]

    # ─── Episodic Memory (JSON logs) ────────────────────────────────────────
    def _log_episode(self, role: str, content: str) -> None:
        """Append a turn to today's JSON log file."""
        today = datetime.now().strftime("%Y-%m-%d")
        log_file = self.history_dir / f"{today}.json"

        entry = {
            "timestamp": datetime.now().isoformat(),
            "role": role,
            "content": content[:500],   # Truncate long content to save space
        }

        try:
            if log_file.exists():
                with open(log_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            else:
                data = []

            data.append(entry)

            with open(log_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Could not log episode: {e}")

    def get_today_log(self) -> list[dict]:
        """Load today's conversation log."""
        today = datetime.now().strftime("%Y-%m-%d")
        log_file = self.history_dir / f"{today}.json"
        if log_file.exists():
            with open(log_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return []

    # ─── User Profile ────────────────────────────────────────────────────────
    def _load_profile(self) -> dict:
        """Load or create user profile."""
        if self.profile_file.exists():
            with open(self.profile_file, "r", encoding="utf-8") as f:
                return json.load(f)
        # Default profile
        profile = {
            "name": "User",
            "interaction_count": 0,
            "preferred_tts": "piper",
            "voice_enabled": False,
            "dsa_progress": {
                "completed_topics": [],
                "current_topic": None,
                "total_sessions": 0,
            },
            "preferences": {
                "response_style": "concise",
                "code_language": "python",
                "theme": "dark",
            },
            "created_at": datetime.now().isoformat(),
        }
        self._save_profile(profile)
        return profile

    def _save_profile(self, profile: dict) -> None:
        self.profile_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.profile_file, "w", encoding="utf-8") as f:
            json.dump(profile, f, indent=2, ensure_ascii=False)

    def increment_interactions(self) -> None:
        self.profile["interaction_count"] = self.profile.get("interaction_count", 0) + 1
        self._save_profile(self.profile)

    def get_profile(self) -> dict:
        return self.profile.copy()

    def update_profile(self, key: str, value) -> None:
        self.profile[key] = value
        self._save_profile(self.profile)

    def mark_dsa_complete(self, topic: str) -> None:
        topics = self.profile["dsa_progress"]["completed_topics"]
        if topic not in topics:
            topics.append(topic)
            self.profile["dsa_progress"]["total_sessions"] += 1
            self._save_profile(self.profile)
