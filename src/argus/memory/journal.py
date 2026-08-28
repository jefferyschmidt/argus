import sqlite3


class JournalStore:
    """Freeform spoken-thought log -- README roadmap item 19. Deliberately
    separate from episodic memory (that's conversation with Argus; this is
    the user thinking out loud with Argus as a scribe, not a participant)
    and from core memory (a journal entry isn't a standing fact to inject
    into every future conversation)."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def add(self, text: str) -> int:
        cur = self.conn.execute("INSERT INTO journal_entries (text) VALUES (?)", (text,))
        self.conn.commit()
        return cur.lastrowid

    def list_recent(self, limit: int = 20) -> list[sqlite3.Row]:
        rows = self.conn.execute(
            "SELECT id, ts, text FROM journal_entries ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return list(reversed(rows))

    def search(self, query: str, limit: int = 20) -> list[sqlite3.Row]:
        rows = self.conn.execute(
            "SELECT id, ts, text FROM journal_entries WHERE text LIKE ? ORDER BY id DESC LIMIT ?",
            (f"%{query}%", limit),
        ).fetchall()
        return list(reversed(rows))
