"""PRD.md §4.1. Persisted, continuously open/closed tracking of "things
not yet resolved" -- the world model's memory of loose ends.

CONNECTION DISCIPLINE (P1) -- read before wiring this up. This class takes
an injected connection and is deliberately agnostic about which one, so
the concurrency decision belongs to whoever constructs it. Today only
tests do, on a plain argus.db connection, which is safe because tests are
single-threaded.

That stops being true the moment reap() is put on its timer
(settings.thread_reap_seconds, Appendix A.1) or the world model is read
from the UI/salience threads. argus.db's shared connection
(memory.store.get_connection) is only safe for concurrent use because
VoiceLoop's _interaction_lock serializes every caller of it -- a reap
timer is not covered by that lock, and a per-component lock here would
not help either, since a sqlite3 Connection is not safe for interleaved
use by two components holding two different locks.

So: whoever first constructs a ThreadStore for production use must give
it a DEDICATED connection with the spine's treatment (own connection
object, own lock, WAL) -- see spine/store.py -- not the shared argus.db
one. Recorded as a requirement in PRD §4.1 rather than pre-built here,
because nothing constructs it yet and there is no live bug to fix.

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
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)

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
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def open(
        self, kind: str, title: str, *, subject: str | None = None,
        close_condition: dict | None = None, opened_by_obs_id: int | None = None,
        metadata: dict | None = None,
    ) -> int:
        now = time.time()
        cur = self.conn.execute(
            "INSERT INTO threads (kind, title, subject, opened_ts, opened_by_obs_id, "
            "close_condition, last_activity_ts, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (kind, title, subject, now, opened_by_obs_id,
             json.dumps(close_condition or {}), now, json.dumps(metadata or {})),
        )
        self.conn.commit()
        return cur.lastrowid

    def close(self, thread_id: int, reason: str) -> bool:
        cur = self.conn.execute(
            "UPDATE threads SET closed_ts = ?, closed_reason = ? WHERE id = ? AND closed_ts IS NULL",
            (time.time(), reason, thread_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def open_threads(self, *, kind: str | None = None, limit: int = 50) -> list[Thread]:
        clauses, params = ["closed_ts IS NULL"], []
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        sql = f"SELECT * FROM threads WHERE {' AND '.join(clauses)} ORDER BY last_activity_ts DESC LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()
        return [_row_to_thread(r) for r in rows]

    def touch(self, thread_id: int) -> None:
        self.conn.execute("UPDATE threads SET last_activity_ts = ? WHERE id = ?", (time.time(), thread_id))
        self.conn.commit()

    def find_open(self, kind: str, subject: str) -> "Thread | None":
        row = self.conn.execute(
            "SELECT * FROM threads WHERE kind = ? AND subject = ? AND closed_ts IS NULL "
            "ORDER BY opened_ts DESC LIMIT 1",
            (kind, subject),
        ).fetchone()
        return _row_to_thread(row) if row else None

    def get(self, thread_id: int) -> "Thread | None":
        row = self.conn.execute("SELECT * FROM threads WHERE id = ?", (thread_id,)).fetchone()
        return _row_to_thread(row) if row else None

    def reap(self, spine, now: float | None = None) -> int:
        """Evaluates every open thread's close_condition (Appendix A.1).
        Called on a timer (settings.thread_reap_seconds) and immediately
        after any observation whose kind appears in an open thread's
        condition -- never on every observation (A.1 evaluation cadence).
        Returns how many threads it closed."""
        from argus.world.predicates import evaluate

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
