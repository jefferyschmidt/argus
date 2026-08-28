"""Periodically distills new episodic conversation into durable core-memory
candidates on the cheap local model tier -- keeps memory feeling like
Argus's own accumulated knowledge over time (the 2026-08-28 "best-ever
memory system" design goal), without the token cost of injecting
ever-growing raw history into every frontier-model call.

Nothing is ever auto-confirmed: every distilled fact goes through the same
propose/review flow (CoreMemoryStore.propose -> "argus memory review") as
any other agent-proposed core memory. The raw episodes this reads from
aren't touched or deleted -- full detail stays searchable via the
episodic/semantic stores even after a compact version has entered core
memory, per the "nothing truly forgotten" design goal."""

import logging

from argus.llm.base import Message, Tier
from argus.ui import events as ui_events

log = logging.getLogger(__name__)

_PROMPT = """Below is a chunk of recent conversation between a user and their personal \
AI assistant, Argus. Extract only genuinely durable facts about the user worth \
remembering long-term -- stable preferences, relationships, ongoing projects, \
recurring constraints. NOT one-off requests, small talk, or anything already \
obviously transient (today's weather, a single reminder, etc).

Reply with each distinct fact on its own line, written as a short standalone \
statement (e.g. "Prefers concise replies, under 40 words by default."). If \
there's nothing durable worth keeping, reply with exactly: NONE

Conversation:
{transcript}"""

_MAX_FACT_CHARS = 200
_MIN_TRANSCRIPT_CHARS = 200  # not worth a model call for a couple of short turns


def _format_transcript(rows) -> str:
    return "\n".join(f"{r['role']}: {r['content']}" for r in rows)


def get_cursor(conn) -> int:
    row = conn.execute("SELECT last_episode_id FROM consolidation_state WHERE id = 1").fetchone()
    return row["last_episode_id"] if row else 0


def set_cursor(conn, episode_id: int) -> None:
    conn.execute("UPDATE consolidation_state SET last_episode_id = ? WHERE id = 1", (episode_id,))
    conn.commit()


def consolidate(router, memory_manager) -> list[str]:
    """Reads episodes since the last run, proposes any durable facts found
    as pending core memories, and advances the cursor. Returns the facts
    proposed (empty if there was nothing new, nothing durable, or the
    local model call failed -- the cursor isn't advanced on failure, so a
    transient error just gets retried next poll)."""
    since_id = get_cursor(memory_manager.conn)
    rows = memory_manager.episodic.all_since_id(since_id)
    if not rows:
        return []

    new_cursor = rows[-1]["id"]
    transcript = _format_transcript(rows)
    if len(transcript) < _MIN_TRANSCRIPT_CHARS:
        set_cursor(memory_manager.conn, new_cursor)
        return []

    try:
        result = router.complete(
            [Message(role="user", content=_PROMPT.format(transcript=transcript))],
            force_tier=Tier.LOCAL,
        )
    except Exception:
        log.exception("Memory consolidation LLM call failed -- will retry next poll")
        return []

    set_cursor(memory_manager.conn, new_cursor)

    text = (result.text or "").strip()
    if not text or text.upper() == "NONE":
        return []

    facts = []
    for line in text.splitlines():
        fact = line.strip(" -*\t")
        if fact and len(fact) <= _MAX_FACT_CHARS:
            facts.append(fact)

    for fact in facts:
        memory_id = memory_manager.core.propose(fact, source="consolidation")
        # Same event orchestrator._propose_core_memory publishes -- so a
        # consolidation-distilled fact shows up in the console's existing
        # confirm/reject review UI exactly like any other agent-proposed
        # core memory, not a second, separate review surface.
        if memory_id:
            ui_events.publish({"type": "core_memory_pending", "id": memory_id, "text": fact})

    return facts
