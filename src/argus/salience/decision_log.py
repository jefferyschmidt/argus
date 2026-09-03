"""PRD.md §7.6 (G-h introspection): `explain_last_action` must return the
`Decision.reason` that actually drove the action -- but Decision itself
is ephemeral, computed and discarded per candidate (§5.1's exact shape
carries no id, no persistence). This is the persisted record that makes
"why did you do that?" answerable after the fact. Own connection + lock
+ WAL (P1, generalized rule)."""

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from argus.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS decision_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    kind TEXT NOT NULL, subject TEXT,
    action TEXT NOT NULL, reason TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_decision_log_ts ON decision_log(ts);
"""


@dataclass
class LoggedDecision:
    id: int
    ts: float
    kind: str
    subject: str | None
    action: str
    reason: str


def _row_to_decision(row: sqlite3.Row) -> LoggedDecision:
    return LoggedDecision(
        id=row["id"], ts=row["ts"], kind=row["kind"], subject=row["subject"],
        action=row["action"], reason=row["reason"],
    )


class DecisionLog:
    def __init__(self, db_path: Path | None = None) -> None:
        self._path = db_path or (settings.data_dir / "argus.db")
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            # PRD §19 unit 43a: without this, a writer that meets a lock
            # held by another connection to the SAME file raises "database
            # is locked" IMMEDIATELY under WAL, instead of waiting -- this
            # store's own self._lock only serializes threads within THIS
            # connection, not across the several other stores that each
            # open their own connection to the same argus.db. Diagnosed as
            # the real cause of a "database is locked" flake that
            # "reproduced clean on rerun" three times.
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def record(self, *, kind: str, subject: str | None, action: str, reason: str, ts: float | None = None) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO decision_log (ts, kind, subject, action, reason) VALUES (?, ?, ?, ?, ?)",
                (ts if ts is not None else time.time(), kind, subject, action, reason),
            )
            self._conn.commit()
            return cur.lastrowid

    def last(self) -> "LoggedDecision | None":
        with self._lock:
            row = self._conn.execute("SELECT * FROM decision_log ORDER BY id DESC LIMIT 1").fetchone()
        return _row_to_decision(row) if row else None

    def recent(self, limit: int = 20) -> list[LoggedDecision]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM decision_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [_row_to_decision(r) for r in rows]
