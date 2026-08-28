import uuid
from concurrent.futures import ThreadPoolExecutor

from argus.memory.core import CoreMemoryStore
from argus.memory.episodic import EpisodicStore
from argus.memory.semantic import SemanticStore
from argus.memory.store import get_connection


class MemoryManager:
    def __init__(self, session_id: str | None = None):
        self.session_id = session_id or str(uuid.uuid4())
        self.conn = get_connection()
        self.core = CoreMemoryStore(self.conn)
        self.episodic = EpisodicStore(self.conn)
        self.semantic = SemanticStore()
        # Embedding a turn is CPU work that doesn't need to finish before we
        # reply -- only queries (build_context) need to be synchronous.
        self._embed_pool = ThreadPoolExecutor(max_workers=1)

    def remember_turn(self, role: str, content: str) -> None:
        episode_id = self.episodic.add(self.session_id, role, content)
        self._embed_pool.submit(
            self.semantic.add,
            doc_id=f"episode-{episode_id}",
            text=content,
            metadata={"role": role, "session_id": self.session_id},
        )

    def build_context(self, query: str, recent_turns: int = 12) -> str:
        """Assembles the memory block to inject ahead of the live conversation:
        core memories verbatim, then relevant semantic hits, then recent turns."""
        parts = []

        core = self.core.list_confirmed()
        if core:
            parts.append("# Core memories (always true, do not contradict)\n" + "\n".join(f"- {c}" for c in core))

        hits = [h for h in self.semantic.search(query, n_results=5) if h["distance"] < 1.2]
        if hits:
            parts.append(
                "# Relevant past context\n" + "\n".join(f"- {h['text']}" for h in hits)
            )

        recent = self.episodic.recent(self.session_id, limit=recent_turns)
        if recent:
            parts.append(
                "# Recent conversation\n"
                + "\n".join(f"{r['role']}: {r['content']}" for r in recent)
            )

        return "\n\n".join(parts)

    def stats(self) -> dict:
        episodic_count = self.conn.execute(
            "SELECT COUNT(*) FROM episodes WHERE session_id = ?", (self.session_id,)
        ).fetchone()[0]
        return {
            "core": len(self.core.list_confirmed()),
            "semantic": self.semantic._collection.count(),
            "episodic": episodic_count,
        }

    def export_all(self) -> dict:
        """"What do you know about me" made concrete and exhaustive --
        everything Argus has stored, across every session, not just the
        top-N relevance hits normal conversation uses. Used by
        `argus memory export`."""
        return {
            "core_confirmed": self.core.list_confirmed(),
            "core_pending": [dict(row) for row in self.core.list_pending()],
            "episodic": [dict(row) for row in self.episodic.all_rows()],
            "semantic": self.semantic.export_all(),
        }

    def forget_everything_except_core(self) -> dict:
        """Purges episodic + semantic memory (conversation history and its
        embeddings). Deliberately leaves core memory untouched -- those
        already have their own explicit confirm/reject review flow
        (argus memory review), and a blanket wipe silently taking out
        standing facts the user confirmed on purpose would be surprising.
        Used by `argus memory forget`."""
        return {
            "episodic_deleted": self.episodic.delete_all(),
            "semantic_deleted": self.semantic.delete_all(),
        }
