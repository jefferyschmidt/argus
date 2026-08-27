import sqlite3


class CoreMemoryStore:
    """High-salience facts that are always injected into context verbatim --
    never summarized, never aged out. Agent-proposed entries start
    unconfirmed so junk doesn't silently join the always-on context; you
    confirm them explicitly (argus memory review)."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def propose(self, content: str, source: str = "agent_proposed") -> int:
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO core_memories (content, source, confirmed) "
            "VALUES (?, ?, 0)",
            (content, source),
        )
        self.conn.commit()
        return cur.lastrowid

    def add_confirmed(self, content: str, source: str = "user") -> int:
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO core_memories (content, source, confirmed) "
            "VALUES (?, ?, 1)",
            (content, source),
        )
        self.conn.commit()
        return cur.lastrowid

    def confirm(self, memory_id: int) -> None:
        self.conn.execute(
            "UPDATE core_memories SET confirmed = 1 WHERE id = ?", (memory_id,)
        )
        self.conn.commit()

    def reject(self, memory_id: int) -> None:
        self.conn.execute("DELETE FROM core_memories WHERE id = ?", (memory_id,))
        self.conn.commit()

    def list_confirmed(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT content FROM core_memories WHERE confirmed = 1 ORDER BY ts"
        ).fetchall()
        return [r["content"] for r in rows]

    def list_pending(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT id, content, ts FROM core_memories WHERE confirmed = 0 ORDER BY ts"
        ).fetchall()
