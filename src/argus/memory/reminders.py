import sqlite3


class ReminderStore:
    """Persisted reminders, surfaced proactively rather than only answered
    once and forgotten -- item 11 on the README roadmap. due_at is an
    ISO8601 string with UTC offset (from datetime.isoformat() on an
    aware datetime); string comparison works for ordering/due-check
    because ISO8601-with-offset sorts lexicographically the same as
    chronologically, as long as every value is written the same way."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def add(self, text: str, due_at_iso: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO reminders (text, due_at) VALUES (?, ?)", (text, due_at_iso)
        )
        self.conn.commit()
        return cur.lastrowid

    def list_pending(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT id, due_at, text FROM reminders WHERE completed = 0 ORDER BY due_at"
        ).fetchall()

    def list_due(self, now_iso: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT id, due_at, text FROM reminders "
            "WHERE completed = 0 AND notified = 0 AND due_at <= ? ORDER BY due_at",
            (now_iso,),
        ).fetchall()

    def mark_notified(self, reminder_id: int) -> None:
        self.conn.execute("UPDATE reminders SET notified = 1 WHERE id = ?", (reminder_id,))
        self.conn.commit()

    def complete(self, reminder_id: int) -> None:
        self.conn.execute("UPDATE reminders SET completed = 1 WHERE id = ?", (reminder_id,))
        self.conn.commit()
