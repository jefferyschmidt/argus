"""PRD.md §19 unit 43a: diagnosed at the unit 37/38 gate as the real
cause of a "database is locked" flake reported three times, always
"reproducing clean on rerun." Several stores each open their OWN
connection to the SAME argus.db file; the per-store threading.Lock (P1)
serializes threads within one connection but does nothing across
connections. Fix: PRAGMA busy_timeout=5000 on every connection at open,
so a writer that meets another connection's lock WAITS (up to 5s)
instead of raising immediately.

Note on the mechanism test below: sqlite3.connect()'s own `timeout`
parameter already defaults to 5.0 seconds, which sets an equivalent
busy handler -- confirmed by reading PRAGMA busy_timeout back
immediately after a bare connect() (see
test_bare_connect_already_defaults_to_a_5_second_busy_timeout below).
None of this codebase's 11 connection sites pass an explicit `timeout`,
so they were never actually running with busy_timeout=0 -- the
observed flake is real, but from ordinary write-lock contention this
default already covers; explicit PRAGMA busy_timeout=5000 makes that
guarantee self-documenting and immune to a future connection call
accidentally overriding it (e.g. `timeout=0`), rather than resting on
an implicit library default nothing in this codebase states outright.
The "fails without / passes with" contrast is proven directly against
the PRAGMA the fix actually sets (busy_timeout=0 vs 5000), not by
diffing this codebase's connections before/after -- since, per the
above, they don't actually differ in this one respect."""

import sqlite3
import threading
import time
from pathlib import Path

import pytest

STORE_CLASSES = [
    ("rules.store", "RuleStore"),
    ("rules.instances", "RuleInstanceStore"),
    ("salience.budget", "InterruptionBudget"),
    ("salience.decision_log", "DecisionLog"),
    ("salience.held", "HeldQueue"),
    ("tasks.store", "TaskStore"),
    ("world.rhythms", "RhythmStore"),
]


def _import(module_suffix: str, name: str):
    import importlib

    module = importlib.import_module(f"argus.{module_suffix}")
    return getattr(module, name)


@pytest.mark.parametrize("module_suffix,class_name", STORE_CLASSES)
def test_every_store_sets_busy_timeout_on_open(tmp_path, module_suffix, class_name):
    """Acceptance: every connection-opening path sets the pragma."""
    cls = _import(module_suffix, class_name)
    store = cls(tmp_path / "argus.db")

    assert store._conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_spine_store_sets_busy_timeout_on_open(tmp_path):
    """SpineStore takes db_path positionally, not keyword, and lives in
    a different module shape than the others -- covered separately."""
    from argus.spine.store import SpineStore

    store = SpineStore(tmp_path / "spine.db")
    assert store._conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_thread_store_sets_busy_timeout_on_open(tmp_path):
    from argus.spine.store import SpineStore
    from argus.world.threads import ThreadStore

    spine = SpineStore(tmp_path / "spine.db")
    store = ThreadStore(spine, tmp_path / "argus.db")
    assert store._conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_escalation_scheduler_sets_busy_timeout_on_open(tmp_path):
    """EscalationScheduler's constructor shape differs from the others
    (threads=, deliver_fn=, db_path=) -- covered separately from the
    generic parametrized list above."""
    from argus.salience.escalation import EscalationScheduler

    store = EscalationScheduler(db_path=tmp_path / "argus.db")
    assert store._conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_get_connection_sets_busy_timeout(tmp_path):
    from argus.memory.store import get_connection

    conn = get_connection(tmp_path / "argus.db")
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


# -- the mechanism itself: fails without, passes with ----------------------

def _hammer_with_busy_timeout(db_path: Path, busy_timeout_ms: int, errors: list, threads: int = 8, hold_seconds: float = 0.2) -> None:
    """N threads, each its own connection, each grabbing an exclusive
    write lock (BEGIN IMMEDIATE) and holding it briefly -- a real,
    guaranteed overlap, not a hopeful race. A barrier lines every thread
    up at the same starting line so the write attempts genuinely
    collide instead of just happening to be close in time."""
    setup = sqlite3.connect(db_path)
    setup.execute("PRAGMA journal_mode=WAL")
    setup.executescript("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, v INTEGER)")
    setup.commit()
    setup.close()

    barrier = threading.Barrier(threads)

    def worker() -> None:
        try:
            conn = sqlite3.connect(db_path, check_same_thread=False)
            conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
            conn.execute("PRAGMA journal_mode=WAL")
            try:
                barrier.wait(timeout=10)
            except threading.BrokenBarrierError:
                pass
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO t (v) VALUES (1)")
            time.sleep(hold_seconds)
            conn.commit()
            conn.close()
        except sqlite3.OperationalError as e:
            errors.append(str(e))
            try:
                barrier.abort()
            except Exception:
                pass

    workers = [threading.Thread(target=worker) for _ in range(threads)]
    for w in workers:
        w.start()
    for w in workers:
        w.join(timeout=20)


def test_concurrent_writers_without_busy_timeout_raise_database_is_locked(tmp_path):
    """The failure mode this unit fixes, reproduced directly: several
    threads writing the same DB via separate connections, none of them
    willing to wait for a lock, raise "database is locked"."""
    errors = []
    _hammer_with_busy_timeout(tmp_path / "test.db", busy_timeout_ms=0, errors=errors)

    assert errors
    assert all("database is locked" in e for e in errors)


def test_concurrent_writers_with_busy_timeout_raise_nothing(tmp_path):
    """Same threads, same contention, same overlap -- the only
    difference is PRAGMA busy_timeout=5000. This is the unit's
    acceptance box: N threads writing the same DB via separate
    connections raise no "database is locked"."""
    errors = []
    _hammer_with_busy_timeout(tmp_path / "test.db", busy_timeout_ms=5000, errors=errors)

    assert errors == []


def test_every_sqlite3_connect_call_site_sets_busy_timeout_explicitly():
    """The genuinely discriminating regression check for "every
    connection-opening path sets the pragma": since
    sqlite3.connect()'s own default already provides an equivalent
    busy handler (see test_bare_connect_already_defaults_to_a_5_second_
    busy_timeout below), a RUNTIME PRAGMA busy_timeout readback on a
    live store instance can't actually tell "explicit pragma present"
    apart from "relying on the unstated library default" -- confirmed
    directly: temporarily deleting the explicit
    self._conn.execute("PRAGMA busy_timeout=5000") line from RuleStore
    left every runtime-readback assertion in this file still passing.
    This inspects the source instead: every sqlite3.connect( call site
    in src/argus must be followed, within a few lines, by an explicit
    PRAGMA busy_timeout statement."""
    import re

    src_root = Path(__file__).resolve().parents[1] / "src" / "argus"
    connect_pattern = re.compile(r"sqlite3\.connect\(")
    pragma_pattern = re.compile(r"PRAGMA busy_timeout")

    missing = []
    checked = 0
    for path in src_root.rglob("*.py"):
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if connect_pattern.search(line):
                checked += 1
                window = "\n".join(lines[i:i + 15])
                if not pragma_pattern.search(window):
                    missing.append(f"{path.relative_to(src_root)}:{i + 1}")

    assert checked >= 11, f"expected at least the 11 known sqlite3.connect() call sites, found {checked}"
    assert missing == [], f"sqlite3.connect() call site(s) with no nearby explicit PRAGMA busy_timeout: {missing}"


def test_bare_connect_already_defaults_to_a_5_second_busy_timeout(tmp_path):
    """Documents why the mechanism test above sets busy_timeout=0
    explicitly for its "without" arm, rather than just omitting the
    pragma: sqlite3.connect()'s own `timeout` parameter already
    defaults to 5.0 seconds and sets an equivalent busy handler, so a
    bare connect() (what every real call site in this codebase already
    does) was never actually running with busy_timeout=0 in the first
    place. PRAGMA busy_timeout=5000 makes that guarantee explicit and
    self-documenting rather than resting on an unstated library
    default."""
    conn = sqlite3.connect(tmp_path / "x.db", check_same_thread=False)
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
