"""PRD.md §19 unit 43a-ii. One shared connect+WAL+schema helper that
every sqlite3 store in this codebase routes through, replacing 11
copy-pasted call sites.

Two problems, one fix:

1. Unit 43a set PRAGMA busy_timeout=5000 at every site, but that alone
   didn't close the "database is locked ... reproduces clean on rerun"
   flake -- measured at the 43a gate: several connections racing
   PRAGMA journal_mode=WAL against a *brand-new* file contend on a
   mode-transition lock the busy handler does not retry (8 threads ->
   ~7-9/10 failures regardless of busy_timeout). Units 37/44 added
   write pressure that made this bite more often, not less. The fix is
   an in-process lock, keyed by resolved file path, serializing the
   one-time WAL transition for that file across every store that opens
   a connection to it -- plus a small bounded retry as belt-and-braces
   for whatever a same-process lock doesn't cover.
2. Every one of the 11 call sites had copy-pasted the same
   connect+busy_timeout+WAL+schema+commit block. Consolidating it here
   is the structural win: the next store literally cannot forget the
   pragmas, because there's only one place they're set."""

import sqlite3
import threading
import time
from pathlib import Path

# One lock per resolved db file path -- not one global lock -- so stores
# backed by different files (e.g. a test's tmp_path fixtures) never
# serialize against each other, only against other connections to the
# SAME file. Guarded by a meta-lock since the dict itself is populated
# lazily from whichever thread first opens a given path (store
# construction is not always on one thread -- ProactiveEngine builds
# several stores, and tests build many stores concurrently).
_wal_locks: dict[str, threading.Lock] = {}
_wal_locks_meta_lock = threading.Lock()

_WAL_RETRY_ATTEMPTS = 5
_WAL_RETRY_BASE_SECONDS = 0.05


def _wal_lock_for(resolved_path: str) -> threading.Lock:
    with _wal_locks_meta_lock:
        lock = _wal_locks.get(resolved_path)
        if lock is None:
            lock = threading.Lock()
            _wal_locks[resolved_path] = lock
        return lock


def open_db(path: Path, schema: str) -> sqlite3.Connection:
    """Opens one connection to `path` with row_factory=sqlite3.Row,
    busy_timeout=5000, serializes this file's one-time WAL-mode
    transition against every other store opening the same file, and
    applies `schema`. The returned connection is ready to use -- no
    further pragma/schema setup needed at the call site."""
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # PRD §19 unit 43a: without this, a writer that meets a lock held by
    # another connection to the same file raises "database is locked"
    # immediately under WAL, instead of waiting.
    conn.execute("PRAGMA busy_timeout=5000")

    resolved_path = str(Path(path).resolve())
    with _wal_lock_for(resolved_path):
        # PRD §19 unit 43a-ii: the actual remaining flake. Several
        # connections racing this exact statement against a brand-new
        # file contend on a mode-transition lock busy_timeout does not
        # retry -- the lock above serializes same-process races
        # completely; the retry below is belt-and-braces for whatever
        # it doesn't cover (e.g. a lingering connection from outside
        # this process, or this process's own prior run).
        last_error: sqlite3.OperationalError | None = None
        for attempt in range(_WAL_RETRY_ATTEMPTS):
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                break
            except sqlite3.OperationalError as e:
                last_error = e
                time.sleep(_WAL_RETRY_BASE_SECONDS * (attempt + 1))
        else:
            raise last_error
        conn.executescript(schema)
        conn.commit()
    return conn
