"""PRD.md §3.2. Deliberately does NOT reuse memory/store.py::get_connection
(P1): that connection is only safe for concurrent access because
VoiceLoop's `_interaction_lock` serializes every caller of it, and sensors
have no such lock. The spine gets its own connection, its own
threading.Lock around every access, and WAL mode."""

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path

from argus.config import settings
from argus.spine.observation import Observation

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           REAL    NOT NULL,
    source       TEXT    NOT NULL,
    kind         TEXT    NOT NULL,
    subject      TEXT,
    payload      TEXT    NOT NULL DEFAULT '{}',
    confidence   REAL    NOT NULL DEFAULT 1.0,
    dedupe_key   TEXT    UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_obs_ts        ON observations(ts);
CREATE INDEX IF NOT EXISTS idx_obs_kind_ts   ON observations(kind, ts);
CREATE INDEX IF NOT EXISTS idx_obs_subject   ON observations(subject);
"""


def _row_to_observation(row: sqlite3.Row) -> Observation:
    return Observation(
        source=row["source"],
        kind=row["kind"],
        ts=row["ts"],
        subject=row["subject"],
        payload=json.loads(row["payload"]),
        confidence=row["confidence"],
        dedupe_key=row["dedupe_key"],
    )


class SpineStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self._path = db_path or (
            Path(settings.spine_db_path) if settings.spine_db_path else settings.data_dir / "spine.db"
        )
        self._lock = threading.Lock()
        # check_same_thread=False: sensors each run on their own daemon
        # thread and share this one connection -- safe here because every
        # access below goes through self._lock, unlike memory/store.py's
        # connection (see module docstring, P1).
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def record(self, obs: Observation) -> int | None:
        """Returns the new row id, or None if `dedupe_key` already existed
        (a no-op re-report -- see PRD §3.2, this replaces every in-process
        "already handled" set, P7) or the write failed. Never raises to
        its caller: a sensor failing to write must not kill the sensor."""
        try:
            with self._lock:
                cur = self._conn.execute(
                    "INSERT OR IGNORE INTO observations "
                    "(ts, source, kind, subject, payload, confidence, dedupe_key) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (obs.ts, obs.source, obs.kind, obs.subject, json.dumps(obs.payload), obs.confidence, obs.dedupe_key),
                )
                self._conn.commit()
                if cur.rowcount == 0:
                    return None
                return cur.lastrowid
        except sqlite3.Error:
            log.exception("Failed to record observation (kind=%s, source=%s)", obs.kind, obs.source)
            return None

    def query(
        self, *, kinds: list[str] | None = None, source: str | None = None,
        subject: str | None = None, since: float | None = None,
        until: float | None = None, limit: int = 200,
    ) -> list[Observation]:
        clauses = []
        params: list = []
        if kinds:
            clauses.append(f"kind IN ({','.join('?' * len(kinds))})")
            params.extend(kinds)
        if source is not None:
            clauses.append("source = ?")
            params.append(source)
        if subject is not None:
            clauses.append("subject = ?")
            params.append(subject)
        if since is not None:
            clauses.append("ts >= ?")
            params.append(since)
        if until is not None:
            clauses.append("ts <= ?")
            params.append(until)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM observations {where} ORDER BY ts DESC, id DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_observation(r) for r in rows]

    def latest(self, kind: str, subject: str | None = None) -> Observation | None:
        clauses, params = ["kind = ?"], [kind]
        if subject is not None:
            clauses.append("subject = ?")
            params.append(subject)
        sql = f"SELECT * FROM observations WHERE {' AND '.join(clauses)} ORDER BY ts DESC, id DESC LIMIT 1"
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return _row_to_observation(row) if row else None

    def count(self, *, kind: str | None = None, since: float | None = None) -> int:
        clauses, params = [], []
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        if since is not None:
            clauses.append("ts >= ?")
            params.append(since)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT COUNT(*) FROM observations {where}"
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return row[0]

    def prune(self, older_than_days: int) -> int:
        cutoff = time.time() - older_than_days * 86400
        with self._lock:
            cur = self._conn.execute("DELETE FROM observations WHERE ts < ?", (cutoff,))
            self._conn.commit()
        return cur.rowcount
