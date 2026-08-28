import sqlite3


class EpisodicStore:
    """Raw, timestamped log of everything said. Source of truth for recency
    lookups and for the semantic store's rollup/embedding job."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def add(self, session_id: str, role: str, content: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO episodes (session_id, role, content) VALUES (?, ?, ?)",
            (session_id, role, content),
        )
        self.conn.commit()
        return cur.lastrowid

    def recent(self, session_id: str, limit: int = 20) -> list[sqlite3.Row]:
        rows = self.conn.execute(
            "SELECT role, content, ts FROM episodes "
            "WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return list(reversed(rows))

    def all_since_id(self, since_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT id, role, content, ts FROM episodes WHERE id > ? ORDER BY id",
            (since_id,),
        ).fetchall()

    def all_rows(self) -> list[sqlite3.Row]:
        """Every episode across every session -- used by memory export, not
        by normal conversation (which only ever needs the current
        session's recency, see recent())."""
        return self.conn.execute(
            "SELECT id, session_id, role, content, ts FROM episodes ORDER BY id"
        ).fetchall()

    def delete_all(self) -> int:
        """Purges every episode across every session. Irreversible -- only
        called from the CLI's explicit `argus memory forget` command, never
        from a conversational tool, since an LLM mishearing "forget that"
        as "forget everything" would be a bad way to lose real history."""
        count = self.conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
        self.conn.execute("DELETE FROM episodes")
        self.conn.commit()
        return count
