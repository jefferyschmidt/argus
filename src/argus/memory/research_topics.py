import sqlite3
from datetime import datetime


class ResearchTopicStore:
    """Tracked topics for proactive research digests (README dream/stretch
    item: "tell it what you care about and it periodically surfaces a
    digest unprompted"). Deliberately its own store rather than reusing
    RoutineStore -- a routine fires unconditionally every day and always
    speaks something; a research topic should stay quiet indefinitely if
    nothing genuinely new has happened, which needs last_digest persisted
    so the model can judge "is this actually new" against what it already
    told the user, not just "is it time yet"."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def add(self, topic: str) -> int:
        cur = self.conn.execute("INSERT INTO research_topics (topic) VALUES (?)", (topic,))
        self.conn.commit()
        return cur.lastrowid

    def list_all(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT id, topic, enabled, last_checked_at, last_digest FROM research_topics ORDER BY created_at"
        ).fetchall()

    def list_enabled(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT id, topic, last_digest FROM research_topics WHERE enabled = 1"
        ).fetchall()

    def record_check(self, topic_id: int, when: datetime, digest: str | None) -> None:
        """digest=None means the check ran but found nothing new -- clears
        nothing, last_digest stays whatever it was so the next check still
        has real prior context to compare against."""
        if digest is None:
            self.conn.execute(
                "UPDATE research_topics SET last_checked_at = ? WHERE id = ?",
                (when.isoformat(), topic_id),
            )
        else:
            self.conn.execute(
                "UPDATE research_topics SET last_checked_at = ?, last_digest = ? WHERE id = ?",
                (when.isoformat(), digest, topic_id),
            )
        self.conn.commit()

    def cancel(self, topic_id: int) -> None:
        self.conn.execute("UPDATE research_topics SET enabled = 0 WHERE id = ?", (topic_id,))
        self.conn.commit()
