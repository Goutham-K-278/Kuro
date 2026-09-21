"""
KURO - RAG Retriever
Queries ChromaDB for relevant document chunks using nomic-embed-text.
"""
import logging

from core.config import CHROMA_DIR, EMBED_MODEL, OLLAMA_BASE_URL

logger = logging.getLogger(__name__)


class RAGRetriever:
    """Retrieve relevant document chunks from ChromaDB."""

    def __init__(self):
        self._client = None

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

    def retrieve(self, query: str, collection_name: str = None, top_k: int = 3) -> list[dict]:
        """
        Retrieve top_k relevant chunks for a query.
        If collection_name is None, searches across all collections.
        Returns list of {content, source, score} dicts.
        """
        query_embedding = self._embed(query)
        results = []

        collections_to_search = (
            [collection_name] if collection_name
            else ["dsa", "blender_docs", "personal_notes"]
        )

        for name in collections_to_search:
            try:
                col = self.client.get_collection(name)
                res = col.query(
                    query_embeddings=[query_embedding],
                    n_results=min(top_k, col.count()),
                )
                for i, doc in enumerate(res["documents"][0]):
                    results.append({
                        "content":  doc,
                        "source":   res["metadatas"][0][i].get("source", name),
                        "distance": res["distances"][0][i],
                    })
            except Exception as e:
                import logging
                logging.getLogger(__name__).debug(f"Non-critical error: {e}")  # Collection doesn't exist yet — skip silently

        # Sort by relevance (lower distance = more relevant)
        results.sort(key=lambda x: x["distance"])
        return results[:top_k]

    def _embed(self, text: str) -> list[float]:
        """Embed a query string using nomic-embed-text via Ollama. Fast-fail (5s)."""
        import requests
        try:
            resp = requests.post(
                f"{OLLAMA_BASE_URL}/api/embeddings",
                json={"model": EMBED_MODEL, "prompt": text},
                timeout=5,  # Fast-fail: don't block if Ollama is busy with generation
            )
            resp.raise_for_status()
            return resp.json()["embedding"]
        except Exception as e:
            logger.debug(f"RAG embedding skipped (Ollama busy): {e}")
            return [0.0] * 768
