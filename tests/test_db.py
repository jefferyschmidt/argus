"""PRD.md §19 unit 43a-ii. Unit 43a's PRAGMA busy_timeout=5000 fixed
ordinary write-lock contention but not this: several connections racing
PRAGMA journal_mode=WAL against a *brand-new* file contend on a
mode-transition lock the busy handler does not retry -- measured at the
43a gate as ~7-9/10 failures across 8 threads, regardless of
busy_timeout. This is the actual remaining "database is locked ...
reproduces clean on rerun" flake, and open_db() (argus/db.py) is the
fix: one shared connect+WAL+schema helper serializing that one-time
transition per resolved file path, that all 11 former call sites now
route through instead of copy-pasting their own."""

import sqlite3
import threading

import pytest

from argus.db import open_db

_SCHEMA = "CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, v INTEGER)"


def test_open_db_sets_busy_timeout(tmp_path):
    conn = open_db(tmp_path / "argus.db", _SCHEMA)
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_open_db_sets_wal_mode(tmp_path):
    conn = open_db(tmp_path / "argus.db", _SCHEMA)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_open_db_applies_the_schema(tmp_path):
    conn = open_db(tmp_path / "argus.db", _SCHEMA)
    conn.execute("INSERT INTO t (v) VALUES (1)")
    conn.commit()
    assert conn.execute("SELECT v FROM t").fetchone()[0] == 1


def test_open_db_row_factory_is_sqlite3_row(tmp_path):
    conn = open_db(tmp_path / "argus.db", _SCHEMA)
    conn.execute("INSERT INTO t (v) VALUES (7)")
    conn.commit()
    row = conn.execute("SELECT v FROM t").fetchone()
    assert row["v"] == 7  # sqlite3.Row supports name-based access; a plain tuple would raise


def test_open_db_is_idempotent_against_an_existing_wal_file(tmp_path):
    """A second store opening a file another store already converted to
    WAL must not raise or re-race the transition."""
    path = tmp_path / "argus.db"
    open_db(path, _SCHEMA)
    conn2 = open_db(path, _SCHEMA)  # must not raise
    assert conn2.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


@pytest.mark.parametrize("attempt", range(3))
def test_many_threads_racing_open_db_on_a_fresh_file_raise_no_database_locked(tmp_path, attempt):
    """PRD §19 unit 43a-ii's own acceptance box, verbatim: '8 threads
    racing a fresh file's WAL transition raise no "database is locked"'.
    Parametrized x3 (not just looped once inside a single test) so a
    reintroduced race shows up as a failure pytest reports distinctly,
    rather than one pass hiding behind eight collapsed assertions --
    and because the original flake's whole signature was "reproduces
    clean on rerun," a single lucky pass proves nothing on its own."""
    path = tmp_path / f"fresh-{attempt}.db"
    threads = 8
    barrier = threading.Barrier(threads)
    errors: list[str] = []

    def worker() -> None:
        try:
            barrier.wait(timeout=10)
        except threading.BrokenBarrierError:
            return
        try:
            open_db(path, _SCHEMA)
        except sqlite3.OperationalError as e:
            errors.append(str(e))

    workers = [threading.Thread(target=worker) for _ in range(threads)]
    for w in workers:
        w.start()
    for w in workers:
        w.join(timeout=20)

    assert errors == [], f"open_db() raised under a fresh-file WAL race: {errors}"


def test_different_paths_do_not_serialize_against_each_other():
    """The lock is keyed per resolved file path, not global -- two
    stores backed by different files (the common case: every tmp_path
    fixture in this suite) must not block on each other's WAL setup."""
    from argus.db import _wal_lock_for

    assert _wal_lock_for("a") is not _wal_lock_for("b")
    assert _wal_lock_for("a") is _wal_lock_for("a")
