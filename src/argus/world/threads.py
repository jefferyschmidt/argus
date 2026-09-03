"""PRD.md §4.1. Persisted, continuously open/closed tracking of "things
not yet resolved" -- the world model's memory of loose ends.

CONNECTION DISCIPLINE (P1). ThreadStore manages its own sqlite connection
-- own Connection object, own threading.Lock around every access, WAL
mode -- the same treatment as spine/store.py::SpineStore, and for the
same reason: once reap() is on its timer (settings.thread_reap_seconds,
Appendix A.1) and the world model is read from the UI/salience threads,
a single connection object called from multiple threads without a lock
either raises "database is locked" or corrupts state. It still points at
the same argus.db file as ReminderStore/RoutineStore/etc (no need for a
separate file -- WAL mode tolerates multiple connections to one file
fine); what changes is that this store no longer accepts an injected,
possibly-shared Connection the way those simpler single-thread-caller
stores do.

thread.opened / thread.closed spine emission. open() and close() record
a matching observation onto the spine -- this is what makes the
`thread_closed` predicate type (Appendix A.1) resolvable at all, and
what a future "why is this thread still open" or timeline view would
read. Emission is best-effort: SpineStore.record() already never raises,
and a lost thread.opened/closed observation must never prevent the
thread state change itself from landing.

`sensitivity` is written but never read here or anywhere else in Phases
A-I (PRD §4.1) -- it exists purely so the deferred speaker-identity/
disclosure work doesn't need a migration later. Do not build behavior on
it.

Openers turn an already-detected fact into a thread; none of them judge
anything themselves -- mail importance is Phase C (salience), commitment
detection is "one cheap LLM call at turn end," wired in wherever a turn
actually ends, not here."""

import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from argus.config import settings
from argus.spine.observation import Observation
from argus.spine.store import SpineStore

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS threads (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    kind              TEXT    NOT NULL,   -- email_reply | commitment | system_health | task | manual
    title             TEXT    NOT NULL,
    subject           TEXT,
    opened_ts         REAL    NOT NULL,
    opened_by_obs_id  INTEGER,
    close_condition   TEXT    NOT NULL DEFAULT '{}',
    closed_ts         REAL,
    closed_reason     TEXT,
    last_activity_ts  REAL,
    sensitivity       TEXT    NOT NULL DEFAULT 'normal',
    metadata          TEXT    NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_threads_open ON threads(closed_ts, last_activity_ts);
"""

# PRD §4.1 "Default close conditions by thread kind" table.
DEFAULT_CLOSE_CONDITIONS: dict[str, dict] = {
    "email_reply": {
        "type": "any_of",
        "predicates": [
            {"type": "user_acknowledged"},
            {"type": "observation_seen", "kind": "mail.replied", "subject": "$thread.subject"},
            {"type": "observation_seen", "kind": "mail.deleted", "subject": "$thread.subject"},
            {"type": "timeout", "seconds": 1209600},
        ],
    },
    "commitment": {
        "type": "any_of",
        "predicates": [
            {"type": "user_acknowledged"},
            {"type": "timeout", "seconds": 2592000},
        ],
    },
    # A broken credential closes when it actually works again, which only
    # the sensor can determine -- it calls close() directly.
    "system_health": {"type": "manual_only"},
    "task": {
        "type": "any_of",
        "predicates": [
            {"type": "observation_seen", "kind": "task.finished"},
            {"type": "observation_seen", "kind": "task.failed"},
        ],
    },
}


@dataclass
class Thread:
    id: int
    kind: str
    title: str
    subject: str | None
    opened_ts: float
    opened_by_obs_id: int | None
    close_condition: dict
    closed_ts: float | None
    closed_reason: str | None
    last_activity_ts: float | None
    sensitivity: str
    metadata: dict


def _row_to_thread(row: sqlite3.Row) -> Thread:
    return Thread(
        id=row["id"], kind=row["kind"], title=row["title"], subject=row["subject"],
        opened_ts=row["opened_ts"], opened_by_obs_id=row["opened_by_obs_id"],
        close_condition=json.loads(row["close_condition"]),
        closed_ts=row["closed_ts"], closed_reason=row["closed_reason"],
        last_activity_ts=row["last_activity_ts"], sensitivity=row["sensitivity"],
        metadata=json.loads(row["metadata"]),
    )


class ThreadStore:
    def __init__(self, spine: SpineStore, db_path: Path | None = None) -> None:
        self.spine = spine
        self._path = db_path or (settings.data_dir / "argus.db")
        self._lock = threading.Lock()
        # check_same_thread=False: this store's own lock (below) is what
        # makes cross-thread use safe, the same reasoning as SpineStore.
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

    def open(
        self, kind: str, title: str, *, subject: str | None = None,
        close_condition: dict | None = None, opened_by_obs_id: int | None = None,
        metadata: dict | None = None,
    ) -> int:
        now = time.time()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO threads (kind, title, subject, opened_ts, opened_by_obs_id, "
                "close_condition, last_activity_ts, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (kind, title, subject, now, opened_by_obs_id,
                 json.dumps(close_condition or {}), now, json.dumps(metadata or {})),
            )
            self._conn.commit()
            thread_id = cur.lastrowid

        self.spine.record(Observation(
            source="world.threads", kind="thread.opened", ts=now, subject=subject,
            payload={"thread_id": thread_id, "kind": kind, "title": title},
        ))
        return thread_id

    def close(self, thread_id: int, reason: str) -> bool:
        now = time.time()
        with self._lock:
            cur = self._conn.execute(
                "UPDATE threads SET closed_ts = ?, closed_reason = ? WHERE id = ? AND closed_ts IS NULL",
                (now, reason, thread_id),
            )
            self._conn.commit()
            closed = cur.rowcount > 0

        if closed:
            self.spine.record(Observation(
                source="world.threads", kind="thread.closed", ts=now,
                payload={"thread_id": thread_id, "reason": reason},
            ))
        return closed

    def open_threads(self, *, kind: str | None = None, limit: int = 50) -> list[Thread]:
        clauses, params = ["closed_ts IS NULL"], []
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        sql = f"SELECT * FROM threads WHERE {' AND '.join(clauses)} ORDER BY last_activity_ts DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_thread(r) for r in rows]

    def touch(self, thread_id: int) -> None:
        with self._lock:
            self._conn.execute("UPDATE threads SET last_activity_ts = ? WHERE id = ?", (time.time(), thread_id))
            self._conn.commit()

    def find_open(self, kind: str, subject: str) -> "Thread | None":
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM threads WHERE kind = ? AND subject = ? AND closed_ts IS NULL "
                "ORDER BY opened_ts DESC LIMIT 1",
                (kind, subject),
            ).fetchone()
        return _row_to_thread(row) if row else None

    def get(self, thread_id: int) -> "Thread | None":
        with self._lock:
            row = self._conn.execute("SELECT * FROM threads WHERE id = ?", (thread_id,)).fetchone()
        return _row_to_thread(row) if row else None

    def reap(self, spine: SpineStore | None = None, now: float | None = None) -> int:
        """Evaluates every open thread's close_condition (Appendix A.1).
        Called on a timer (settings.thread_reap_seconds) and immediately
        after any observation whose kind appears in an open thread's
        condition -- never on every observation (A.1 evaluation cadence).
        Returns how many threads it closed.

        `spine` defaults to self.spine -- kept as an override parameter
        for tests/callers that want to evaluate against a different
        SpineStore than the one this ThreadStore emits thread.opened/
        thread.closed onto."""
        from argus.world.predicates import evaluate

        spine = spine if spine is not None else self.spine
        now = now if now is not None else time.time()
        closed = 0
        for thread in self.open_threads(limit=100_000):
            try:
                satisfied = evaluate(thread.close_condition, thread=thread, spine=spine, now=now)
            except Exception:
                # A predicate must fail open, not take the reaper down --
                # evaluate() already fails open on bad *data*; this is the
                # backstop for anything it can't anticipate.
                log.exception("close_condition evaluation failed for thread %s -- leaving it open", thread.id)
                continue
            if satisfied and self.close(thread.id, reason="close_condition satisfied"):
                closed += 1
        return closed

    # -- Openers (§4.1) --------------------------------------------------

    def open_email_reply(self, *, sender: str, mail_subject: str, obs_id: int | None = None) -> int:
        existing = self.find_open("email_reply", sender)
        if existing:
            self.touch(existing.id)
            return existing.id
        return self.open(
            "email_reply", title=f"Reply to {sender}: {mail_subject}", subject=sender,
            close_condition=DEFAULT_CLOSE_CONDITIONS["email_reply"],
            opened_by_obs_id=obs_id, metadata={"mail_subject": mail_subject},
        )

    def open_commitment(self, *, text: str, obs_id: int | None = None) -> int:
        return self.open(
            "commitment", title=text,
            close_condition=DEFAULT_CLOSE_CONDITIONS["commitment"], opened_by_obs_id=obs_id,
        )

    def open_system_health(self, *, subject: str, description: str, obs_id: int | None = None) -> int:
        # Deduped by subject so a repeated failure touches one thread
        # instead of opening hundreds (§4.1).
        existing = self.find_open("system_health", subject)
        if existing:
            self.touch(existing.id)
            return existing.id
        return self.open(
            "system_health", title=description, subject=subject,
            close_condition=DEFAULT_CLOSE_CONDITIONS["system_health"], opened_by_obs_id=obs_id,
        )
