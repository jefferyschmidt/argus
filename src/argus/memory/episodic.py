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
