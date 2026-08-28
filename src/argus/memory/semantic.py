import chromadb

from argus.config import settings


class SemanticStore:
    """Vector-searchable memory for 'have we talked about this before'
    lookups that recency-based episodic search can't do. Uses Chroma's
    default local embedding model (all-MiniLM-L6-v2, CPU, no API calls)."""

    def __init__(self, collection_name: str = "argus_memory"):
        self._client = chromadb.PersistentClient(path=str(settings.data_dir / "chroma"))
        self._collection_name = collection_name
        self._collection = self._client.get_or_create_collection(collection_name)

    def add(self, doc_id: str, text: str, metadata: dict) -> None:
        self._collection.upsert(ids=[doc_id], documents=[text], metadatas=[metadata])

    def export_all(self) -> list[dict]:
        """Every stored document + metadata -- used by memory export, not
        by normal conversation (which only ever needs top-N relevance hits
        via search())."""
        if self._collection.count() == 0:
            return []
        result = self._collection.get(include=["documents", "metadatas"])
        return [
            {"id": doc_id, "text": doc, "metadata": meta}
            for doc_id, doc, meta in zip(result["ids"], result["documents"], result["metadatas"])
        ]

    def delete_all(self) -> int:
        """Purges the entire collection. Irreversible -- only called from
        the CLI's explicit `argus memory forget` command, never from a
        conversational tool (see EpisodicStore.delete_all for why)."""
        count = self._collection.count()
        self._client.delete_collection(self._collection_name)
        self._collection = self._client.get_or_create_collection(self._collection_name)
        return count

    def search(self, query: str, n_results: int = 5) -> list[dict]:
        if self._collection.count() == 0:
            return []
        results = self._collection.query(
            query_texts=[query], n_results=min(n_results, self._collection.count())
        )
        out = []
        for doc, meta, dist in zip(
            results["documents"][0], results["metadatas"][0], results["distances"][0]
        ):
            out.append({"text": doc, "metadata": meta, "distance": dist})
        return out
