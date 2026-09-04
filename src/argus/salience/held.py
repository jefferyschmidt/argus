"""PRD.md §5.4. Held items are delivered at the next natural pause,
folded into a briefing, or dismissed -- nothing is ever silently
dropped, the specific defect Phase C exists to remove. Phase H renders
this table; until then `argus held` (cli.py) is sufficient."""

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from argus.config import settings
from argus.db import open_db

SCHEMA = """
CREATE TABLE IF NOT EXISTS held_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_ts REAL NOT NULL, kind TEXT NOT NULL, subject TEXT,
    text TEXT NOT NULL, score REAL NOT NULL,
    thread_id INTEGER, delivered_ts REAL, dismissed_ts REAL
);
CREATE INDEX IF NOT EXISTS idx_held_items_pending ON held_items(delivered_ts, dismissed_ts);
"""


@dataclass
class HeldItem:
    id: int
    created_ts: float
    kind: str
    subject: str | None
    text: str
    score: float
    thread_id: int | None
    delivered_ts: float | None
    dismissed_ts: float | None


def _row_to_item(row: sqlite3.Row) -> HeldItem:
    return HeldItem(
        id=row["id"], created_ts=row["created_ts"], kind=row["kind"], subject=row["subject"],
        text=row["text"], score=row["score"], thread_id=row["thread_id"],
        delivered_ts=row["delivered_ts"], dismissed_ts=row["dismissed_ts"],
    )


class HeldQueue:
    def __init__(self, db_path: Path | None = None) -> None:
        self._path = db_path or (settings.data_dir / "argus.db")
        self._lock = threading.Lock()
        # PRD §19 unit 43a/43a-ii: open_db() sets busy_timeout and
        # serializes this file's one-time WAL transition against every
        # other store that opens a connection to the same argus.db (see
        # db.py) -- this store's own self._lock only serializes threads
        # within THIS connection.
        self._conn = open_db(self._path, SCHEMA)

    def add(self, *, kind: str, subject: str | None, text: str, score: float, thread_id: int | None = None) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO held_items (created_ts, kind, subject, text, score, thread_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (time.time(), kind, subject, text, score, thread_id),
            )
            self._conn.commit()
            return cur.lastrowid

    def pending(self, limit: int = 50) -> list[HeldItem]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM held_items WHERE delivered_ts IS NULL AND dismissed_ts IS NULL "
                "ORDER BY score DESC, created_ts LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_item(r) for r in rows]

    def list_dismissed(self, since: float | None = None, limit: int = 1000) -> list[HeldItem]:
        """G4 induction (PRD §7.5) reads this looking for "repeated
        dismissals of the same kind" -- a pattern worth proposing a
        suppression rule for."""
        clauses, params = ["dismissed_ts IS NOT NULL"], []
        if since is not None:
            clauses.append("dismissed_ts >= ?")
            params.append(since)
        sql = f"SELECT * FROM held_items WHERE {' AND '.join(clauses)} ORDER BY dismissed_ts DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_item(r) for r in rows]

    def mark_delivered(self, item_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE held_items SET delivered_ts = ? WHERE id = ? AND delivered_ts IS NULL AND dismissed_ts IS NULL",
                (time.time(), item_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def dismiss(self, item_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE held_items SET dismissed_ts = ? WHERE id = ? AND delivered_ts IS NULL AND dismissed_ts IS NULL",
                (time.time(), item_id),
            )
            self._conn.commit()
            return cur.rowcount > 0
