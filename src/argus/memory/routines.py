import sqlite3
from datetime import datetime


class RoutineStore:
    """Recurring, time-triggered goals -- README roadmap item 10 ("every
    morning at 7, check X and tell me"). Deliberately generic rather than
    a bespoke "daily briefing" feature: a briefing is just a routine whose
    goal happens to be "check the weather/reminders/email and summarize
    it" -- the same mechanism covers both roadmap items 9 and 10 with no
    separate code path. time_of_day is "HH:MM" 24-hour local time; a
    routine fires once per calendar day at or after that time, tracked via
    last_run_date rather than a precise timestamp so a routine that's due
    while Argus is offline/busy still fires once it's checked again,
    rather than being silently skipped for the day."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def add(self, time_of_day: str, goal: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO routines (time_of_day, goal) VALUES (?, ?)", (time_of_day, goal)
        )
        self.conn.commit()
        return cur.lastrowid

    def list_all(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT id, time_of_day, goal, enabled, last_run_date FROM routines ORDER BY time_of_day"
        ).fetchall()

    def list_due(self, now: datetime) -> list[sqlite3.Row]:
        today = now.date().isoformat()
        current_hm = now.strftime("%H:%M")
        return self.conn.execute(
            "SELECT id, time_of_day, goal FROM routines "
            "WHERE enabled = 1 AND time_of_day <= ? AND (last_run_date IS NULL OR last_run_date != ?)",
            (current_hm, today),
        ).fetchall()

    def mark_run(self, routine_id: int, when: datetime) -> None:
        self.conn.execute(
            "UPDATE routines SET last_run_date = ? WHERE id = ?", (when.date().isoformat(), routine_id)
        )
        self.conn.commit()

    def cancel(self, routine_id: int) -> None:
        self.conn.execute("UPDATE routines SET enabled = 0 WHERE id = ?", (routine_id,))
        self.conn.commit()
