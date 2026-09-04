"""PRD.md §6. Persisted autonomous-task state.

CONNECTION DISCIPLINE (P1, generalized at the U-C4 gate -- PRD §5.2: "a
store reachable from salience, a sensor, a reap timer, or the UI gets
its own connection + lock + WAL, mirroring RuleStore"). TaskStore is the
fourth instance of this: it's written from the task worker pool's own
threads (one per running task, up to max_concurrent_tasks) AND read from
tool calls (task_status, cancel_task) on whichever thread is handling
the current turn -- never route this through memory.store.get_connection(),
which is only safe because VoiceLoop's _interaction_lock serializes every
caller of it, a guarantee that doesn't hold here."""

import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from argus.config import settings
from argus.db import open_db

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL, prompt TEXT NOT NULL,
    status TEXT NOT NULL,            -- queued|running|paused|done|failed|cancelled
    created_ts REAL NOT NULL, started_ts REAL, finished_ts REAL,
    progress_note TEXT, result_ref TEXT, error TEXT,
    created_by TEXT NOT NULL DEFAULT 'user',
    budget_tokens INTEGER NOT NULL, budget_seconds REAL NOT NULL,
    tokens_used INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
"""


@dataclass
class Task:
    id: int
    title: str
    prompt: str
    status: str
    created_ts: float
    started_ts: float | None
    finished_ts: float | None
    progress_note: str | None
    result_ref: str | None
    error: str | None
    created_by: str
    budget_tokens: int
    budget_seconds: float
    tokens_used: int


def _row_to_task(row: sqlite3.Row) -> Task:
    return Task(
        id=row["id"], title=row["title"], prompt=row["prompt"], status=row["status"],
        created_ts=row["created_ts"], started_ts=row["started_ts"], finished_ts=row["finished_ts"],
        progress_note=row["progress_note"], result_ref=row["result_ref"], error=row["error"],
        created_by=row["created_by"], budget_tokens=row["budget_tokens"], budget_seconds=row["budget_seconds"],
        tokens_used=row["tokens_used"],
    )


class TaskStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self._path = db_path or (settings.data_dir / "argus.db")
        self._lock = threading.Lock()
        # PRD §19 unit 43a/43a-ii: open_db() sets busy_timeout and
        # serializes this file's one-time WAL transition against every
        # other store that opens a connection to the same argus.db (see
        # db.py) -- this store's own self._lock only serializes threads
        # within THIS connection.
        self._conn = open_db(self._path, SCHEMA)

    def create(
        self, *, title: str, prompt: str, budget_tokens: int, budget_seconds: float, created_by: str = "user",
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO tasks (title, prompt, status, created_ts, created_by, budget_tokens, budget_seconds) "
                "VALUES (?, ?, 'queued', ?, ?, ?, ?)",
                (title, prompt, time.time(), created_by, budget_tokens, budget_seconds),
            )
            self._conn.commit()
            return cur.lastrowid

    def mark_running(self, task_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE tasks SET status = 'running', started_ts = ? WHERE id = ? AND status = 'queued'",
                (time.time(), task_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def mark_done(self, task_id: int, result_ref: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE tasks SET status = 'done', finished_ts = ?, result_ref = ? "
                "WHERE id = ? AND status = 'running'",
                (time.time(), result_ref, task_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def mark_failed(self, task_id: int, error: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE tasks SET status = 'failed', finished_ts = ?, error = ? "
                "WHERE id = ? AND status IN ('running', 'queued')",
                (time.time(), error, task_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def mark_cancelled(self, task_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE tasks SET status = 'cancelled', finished_ts = ? "
                "WHERE id = ? AND status IN ('running', 'queued')",
                (time.time(), task_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def update_progress(self, task_id: int, note: str, tokens_used: int | None = None) -> None:
        with self._lock:
            if tokens_used is None:
                self._conn.execute("UPDATE tasks SET progress_note = ? WHERE id = ?", (note, task_id))
            else:
                self._conn.execute(
                    "UPDATE tasks SET progress_note = ?, tokens_used = ? WHERE id = ?",
                    (note, tokens_used, task_id),
                )
            self._conn.commit()

    def get(self, task_id: int) -> "Task | None":
        with self._lock:
            row = self._conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return _row_to_task(row) if row else None

    def list_queued(self, limit: int = 50) -> list[Task]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM tasks WHERE status = 'queued' ORDER BY created_ts LIMIT ?", (limit,)
            ).fetchall()
        return [_row_to_task(r) for r in rows]

    def list_running(self, limit: int = 50) -> list[Task]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM tasks WHERE status = 'running' ORDER BY started_ts LIMIT ?", (limit,)
            ).fetchall()
        return [_row_to_task(r) for r in rows]

    def list_all(self, limit: int = 50) -> list[Task]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM tasks ORDER BY created_ts DESC LIMIT ?", (limit,)).fetchall()
        return [_row_to_task(r) for r in rows]
