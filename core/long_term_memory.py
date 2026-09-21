"""
KURO - Long Term Memory (ChromaDB)
Persistent memory for user facts and past conversations.
"""
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path

from core.config import CHROMA_DIR, OLLAMA_BASE_URL, EMBED_MODEL, MEMORY_ENCRYPTION_ENABLED

logger = logging.getLogger(__name__)


class KuroMemory:
    def __init__(self):
        self._client = None
        self._conversations = None
        self._facts = None
        self._fernet = None
        self._enc_enabled = MEMORY_ENCRYPTION_ENABLED
        logger.info("Long-Term Memory initializing (lazy-load ChromaDB)")

    def _get_fernet(self):
        if not self._enc_enabled:
            return None
        if self._fernet is not None:
            return self._fernet
        try:
            from cryptography.fernet import Fernet

            key_env = os.getenv("MEMORY_ENCRYPTION_KEY", "").strip()
            key_path = Path("data") / "kuro_memory" / "memory.key"
            key_path.parent.mkdir(parents=True, exist_ok=True)

            if key_env:
                key = key_env.encode("utf-8")
            elif key_path.exists():
                key = key_path.read_bytes().strip()
            else:
                key = Fernet.generate_key()
                key_path.write_bytes(key)

            self._fernet = Fernet(key)
            return self._fernet
        except Exception as e:
            logger.warning(f"LTM encryption unavailable, using plaintext storage: {e}")
            self._enc_enabled = False
            return None

    def _encrypt_document(self, text: str) -> str:
        f = self._get_fernet()
        if f is None:
            return text
        token = f.encrypt(text.encode("utf-8")).decode("ascii")
        return f"enc:v1:{token}"

    def _decrypt_document(self, text: str) -> str:
        if not isinstance(text, str) or not text.startswith("enc:v1:"):
            return text
        f = self._get_fernet()
        if f is None:
            return text
        token = text[len("enc:v1:"):].encode("ascii")
        try:
            return f.decrypt(token).decode("utf-8", errors="replace")
        except Exception:
            return text

    @property
    def client(self):
        if self._client is None:
            import chromadb
            from chromadb.config import Settings
            self._client = chromadb.PersistentClient(
                path=str(CHROMA_DIR),
                settings=Settings(anonymized_telemetry=False)
            )
        return self._client

    @property
    def conversations(self):
        if self._conversations is None:
            self._conversations = self.client.get_or_create_collection("ltm_conversations")
        return self._conversations

    @property
    def facts(self):
        if self._facts is None:
            self._facts = self.client.get_or_create_collection("ltm_user_facts")
        return self._facts

    def _embed(self, text: str) -> list[float]:
        """Embed text using local nomic-embed-text via Ollama. Fast-fail (5s) to avoid blocking."""
        import requests
        try:
            resp = requests.post(
                f"{OLLAMA_BASE_URL}/api/embeddings",
                json={"model": EMBED_MODEL, "prompt": text},
                timeout=5,  # Fast-fail: 5s instead of 60s — don't block the main pipeline
            )
            resp.raise_for_status()
            return resp.json()["embedding"]
        except Exception as e:
            logger.debug(f"LTM Embedding skipped (Ollama busy): {e}")
            return [0.0] * 768  # Fallback zero vector

    def save_conversation(self, user_msg: str, kuro_response: str) -> None:
        """Save a complete interaction turn into long-term memory (async, non-blocking)."""
        import threading

        def _save():
            try:
                combined = f"User: {user_msg}\nKuro: {kuro_response}"
                embedding = self._embed(combined)
                stored_doc = self._encrypt_document(combined)
                self.conversations.add(
                    ids=[str(uuid.uuid4())],
                    embeddings=[embedding],
                    documents=[stored_doc],
                    metadatas=[{"timestamp": datetime.now().isoformat()}]
                )
            except Exception as e:
                logger.warning(f"LTM save_conversation background failed: {e}")

        threading.Thread(target=_save, daemon=True).start()

    def save_fact(self, fact: str, category: str = "general") -> None:
        """Save a specific fact about the user."""
        embedding = self._embed(fact)
        stored_doc = self._encrypt_document(fact)
        
        self.facts.add(
            ids=[str(uuid.uuid4())],
            embeddings=[embedding],
            documents=[stored_doc],
            metadatas=[{"category": category, "timestamp": datetime.now().isoformat()}]
        )
        logger.info(f"Learned new fact: '{fact}'")

    def recall(self, query: str, n: int = 5, collection: str = "conversations") -> list[str]:
        """Find the most relevant memories for a query."""
        try:
            col = self.conversations if collection == "conversations" else self.facts
            if col.count() == 0:
                return []
            
            embedding = self._embed(query)
            # Only query if there are documents in the collection
            results = col.query(
                query_embeddings=[embedding],
                n_results=min(n, col.count())
            )
            docs = results['documents'][0] if results['documents'] else []
            return [self._decrypt_document(d) for d in docs]
        except Exception as e:
            logger.error(f"LTM Recall failed: {e}")
            return []

    def get_context_for_prompt(self, current_message: str) -> str:
        """Build a memory context block to inject into Kuro's system prompt.
        Fast-path: skip entirely if Ollama is likely busy (embedding would block)."""
        # Quick check: is Ollama free? If not, skip LTM to avoid 5s+ delay
        try:
            import requests
            r = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=1)
            if not r.ok:
                return ""
        except Exception:
            return ""  # Ollama unreachable or slow — skip

        # Get relevant facts
        relevant_facts = self.recall(current_message, n=5, collection="facts")
        # Get relevant past conversations
        past_convos = self.recall(current_message, n=3, collection="conversations")
        
        context = ""
        if relevant_facts:
            context += "\n[System Memory - Known User Facts]:\n"
            context += "\n".join(f"- {f}" for f in relevant_facts)
            
        if past_convos:
            context += "\n\n[System Memory - Relevant Past Conversations]:\n"
            context += "\n---\n".join(past_convos)
            
        return context
