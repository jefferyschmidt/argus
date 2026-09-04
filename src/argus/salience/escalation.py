"""PRD.md §5.5. A Decision may carry timed escalation steps ("repeat in
10 min, push after 30" -- Decision.escalation, §5.1). One scheduler
thread owns every pending escalation; each step re-checks whether its
thread has since closed and aborts if so, rather than firing a
now-irrelevant follow-up. Escalations are persisted (own dedicated
connection, own lock, WAL -- the same P1 treatment as every other store
this build has needed once more than one thread touches it) and
reconciled on startup, so a crash between "steps scheduled" and "step
due" doesn't lose them."""

import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from argus.config import settings
from argus.db import open_db
from argus.voice.captions import publish_spoken

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS escalations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_ts REAL NOT NULL,
    fire_ts REAL NOT NULL,
    channel TEXT NOT NULL,       -- speak | ambient | push
    text TEXT NOT NULL,
    thread_id INTEGER,           -- aborts if this thread has since closed
    fired_ts REAL,
    aborted_ts REAL
);
CREATE INDEX IF NOT EXISTS idx_escalations_pending ON escalations(fired_ts, aborted_ts, fire_ts);
"""


@dataclass
class PendingEscalation:
    id: int
    created_ts: float
    fire_ts: float
    channel: str
    text: str
    thread_id: int | None
    fired_ts: float | None
    aborted_ts: float | None


def _row_to_escalation(row: sqlite3.Row) -> PendingEscalation:
    return PendingEscalation(
        id=row["id"], created_ts=row["created_ts"], fire_ts=row["fire_ts"], channel=row["channel"],
        text=row["text"], thread_id=row["thread_id"], fired_ts=row["fired_ts"], aborted_ts=row["aborted_ts"],
    )


class EscalationScheduler:
    def __init__(self, threads=None, deliver_fn=None, db_path: Path | None = None):
        """threads: ThreadStore, used to check whether a step's watched
        thread has since closed. deliver_fn(channel: str, text: str) --
        however this build's caller actually announces/pushes; this class
        owns only the timing and thread-closed-abort logic, not delivery
        mechanics (those already exist wherever speak_fn/push do)."""
        self.threads = threads
        self.deliver_fn = deliver_fn
        self._path = db_path or (settings.data_dir / "argus.db")
        self._lock = threading.Lock()
        # PRD §19 unit 43a/43a-ii: open_db() sets busy_timeout and
        # serializes this file's one-time WAL transition against every
        # other store that opens a connection to the same argus.db (see
        # db.py) -- this store's own self._lock only serializes threads
        # within THIS connection.
        self._conn = open_db(self._path, SCHEMA)
        self._stop = threading.Event()

    def schedule(self, *, text: str, steps: list, thread_id: int | None = None, now: float | None = None) -> list[int]:
        """Persists each EscalationStep (after_seconds, channel) as a
        pending row. Returns their ids."""
        now = now if now is not None else time.time()
        ids = []
        with self._lock:
            for step in steps:
                cur = self._conn.execute(
                    "INSERT INTO escalations (created_ts, fire_ts, channel, text, thread_id) VALUES (?, ?, ?, ?, ?)",
                    (now, now + step.after_seconds, step.channel, text, thread_id),
                )
                ids.append(cur.lastrowid)
            self._conn.commit()
        return ids

    def pending(self) -> list[PendingEscalation]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM escalations WHERE fired_ts IS NULL AND aborted_ts IS NULL ORDER BY fire_ts"
            ).fetchall()
        return [_row_to_escalation(r) for r in rows]

    def process_due(self, now: float | None = None) -> int:
        """Fires every step whose fire_ts has passed -- aborting first if
        its watched thread has since closed. Returns how many fired.
        This is both the timer tick's body and what reconcile-on-startup
        calls, so a step that came due while the process was down still
        fires (or aborts) the next time this runs."""
        now = now if now is not None else time.time()
        fired = 0
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM escalations WHERE fired_ts IS NULL AND aborted_ts IS NULL AND fire_ts <= ?", (now,)
            ).fetchall()
        for row in rows:
            step = _row_to_escalation(row)
            if step.thread_id is not None and self.threads is not None:
                thread = self.threads.get(step.thread_id)
                if thread is None or thread.closed_ts is not None:
                    self._mark(step.id, "aborted_ts", now)
                    continue
            if self.deliver_fn is not None:
                try:
                    # PRD §19 unit 40 Part 2: process_due() calls deliver_fn
                    # directly, bypassing SalienceDispatcher._deliver() --
                    # the only other place a caption/transcript gets
                    # published for spoken output. Without this, an
                    # escalation follow-up's text reached the user's ears
                    # (or console) with nothing captioned for it in
                    # pipeline mode specifically (found at the §19 u41/
                    # u40-Part-1 pipeline-harness gate). Every channel
                    # currently just speaks regardless of its declared
                    # value (see ProactiveEngine's own deliver_fn comment --
                    # channel-specific ambient/push routing isn't built
                    # yet), so this captions unconditionally to match.
                    publish_spoken(step.text)
                    self.deliver_fn(step.channel, step.text)
                except Exception:
                    log.exception("Escalation delivery failed for step %s", step.id)
            self._mark(step.id, "fired_ts", now)
            fired += 1
        return fired

    def _mark(self, escalation_id: int, column: str, ts: float) -> None:
        with self._lock:
            self._conn.execute(f"UPDATE escalations SET {column} = ? WHERE id = ?", (ts, escalation_id))
            self._conn.commit()

    def start(self) -> None:
        """Reconciles anything already due (PRD: "reconciled on
        startup"), then starts the one scheduler thread that owns every
        pending escalation from here on."""
        self.process_due()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.process_due()
            except Exception:
                log.exception("Escalation scheduler tick failed")
            self._stop.wait(settings.escalation_poll_seconds)

    def stop(self) -> None:
        self._stop.set()
