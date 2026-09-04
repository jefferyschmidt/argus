"""PRD.md §5.3. A token bucket capped at settings.interruptions_per_hour,
persisted so a restart does not refill it -- implemented as a sliding
one-hour window of consumption timestamps in sqlite (not an in-memory
counter that resets on process start). `speak` consumes budget;
`ambient` and `hold` do not."""

import threading
import time
from pathlib import Path

from argus.config import settings
from argus.db import open_db

SCHEMA = """
CREATE TABLE IF NOT EXISTS interruption_budget_consumption (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_interruption_budget_ts ON interruption_budget_consumption(ts);
"""


class InterruptionBudget:
    def __init__(self, db_path: Path | None = None) -> None:
        self._path = db_path or (settings.data_dir / "argus.db")
        self._lock = threading.Lock()
        # PRD §19 unit 43a/43a-ii: open_db() sets busy_timeout and
        # serializes this file's one-time WAL transition against every
        # other store that opens a connection to the same argus.db (see
        # db.py) -- this store's own self._lock only serializes threads
        # within THIS connection.
        self._conn = open_db(self._path, SCHEMA)

    def available(self, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        cutoff = now - 3600
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM interruption_budget_consumption WHERE ts > ?", (cutoff,)
            ).fetchone()
        return row[0] < settings.interruptions_per_hour

    def consume(self, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        with self._lock:
            self._conn.execute("INSERT INTO interruption_budget_consumption (ts) VALUES (?)", (now,))
            self._conn.commit()

    def remaining(self, now: float | None = None) -> int:
        now = now if now is not None else time.time()
        cutoff = now - 3600
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM interruption_budget_consumption WHERE ts > ?", (cutoff,)
            ).fetchone()
        return max(0, settings.interruptions_per_hour - row[0])
