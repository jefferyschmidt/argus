import uuid
from concurrent.futures import Future, ThreadPoolExecutor, wait as wait_futures

from argus.memory.core import CoreMemoryStore
from argus.memory.episodic import EpisodicStore
from argus.memory.semantic import SemanticStore
from argus.memory.store import get_connection

# remember_turn used to embed EVERY turn indiscriminately -- confirmed by a
# live diagnostic (300 filler documents like "okay"/"thanks"/"sounds good"
# against 1 real fact) that this measurably dilutes semantic recall at
# realistic scale, part of the "stay cheap and relevant as it grows" memory
# design goal. Episodic storage (recent-turns context) is untouched --
# every turn is still kept there for short-term recency, this only skips
# the embedding pass for turns that would never be worth searching for.
_MIN_EMBED_CHARS = 12
_FILLER_TURNS = {
    "ok", "okay", "thanks", "thank you", "sounds good", "got it", "yeah",
    "sure", "no problem", "sure thing", "alright", "cool", "great", "yep",
    "you're welcome", "youre welcome", "glad to help", "have a good one",
    "catch you later", "bye", "goodbye", "hi", "hello", "hey",
    "is there anything else", "anything else",
}


def _is_worth_embedding(content: str) -> bool:
    text = content.strip()
    if len(text) < _MIN_EMBED_CHARS:
        return False
    return text.lower().strip(" .!?") not in _FILLER_TURNS


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
        self._pending_embeds: list[Future] = []

    def remember_turn(self, role: str, content: str) -> None:
        episode_id = self.episodic.add(self.session_id, role, content)
        if not _is_worth_embedding(content):
            return
        future = self._embed_pool.submit(
            self.semantic.add,
            doc_id=f"episode-{episode_id}",
            text=content,
            metadata={"role": role, "session_id": self.session_id},
        )
        self._pending_embeds.append(future)
        self._pending_embeds = [f for f in self._pending_embeds if not f.done()]

    def flush_pending_embeds(self, timeout: float = 3.0) -> None:
        """Waits (bounded) for queued embeds to finish. Needed before a
        restart: restart.py's os.execv replaces the process image directly,
        bypassing normal interpreter shutdown/atexit entirely, so anything
        still queued in _embed_pool at that moment would otherwise be
        silently lost -- the most recent turn(s) never becoming recallable."""
        if self._pending_embeds:
            wait_futures(self._pending_embeds, timeout=timeout)
            self._pending_embeds = []

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
