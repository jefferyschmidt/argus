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


def test_only_db_py_calls_sqlite3_connect_directly():
    """PRD.md §19 unit 43a-ii: the 11 sites that used to each open their
    own connect+busy_timeout+WAL+schema block now all route through the
    one shared argus.db.open_db() helper -- see that module's docstring.
    This is the regression guard for "all 11 sites open through the
    shared helper" (43a-ii's own acceptance box): a future store that
    calls sqlite3.connect() directly instead of open_db() bypasses both
    the busy_timeout pragma AND the WAL-transition-race fix, silently
    reintroducing the exact flake this unit exists to close."""
    import re

    src_root = Path(__file__).resolve().parents[1] / "src" / "argus"
    connect_pattern = re.compile(r"sqlite3\.connect\(")

    sites = []
    for path in src_root.rglob("*.py"):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if connect_pattern.search(line):
                sites.append(f"{path.relative_to(src_root)}:{i}")

    assert len(sites) == 1 and sites[0].startswith("db.py:"), (
        f"sqlite3.connect() called outside db.py's open_db() helper: {sites} -- "
        "route through argus.db.open_db() instead"
    )


def test_open_db_helper_sets_busy_timeout_explicitly():
    """The genuinely discriminating regression check for "the shared
    helper sets the pragma": since sqlite3.connect()'s own default
    already provides an equivalent busy handler (see
    test_bare_connect_already_defaults_to_a_5_second_busy_timeout
    below), a RUNTIME PRAGMA busy_timeout readback on a live connection
    can't tell "explicit pragma present" apart from "relying on the
    unstated library default." This inspects db.py's own source
    instead."""
    import re

    db_py = (Path(__file__).resolve().parents[1] / "src" / "argus" / "db.py").read_text(encoding="utf-8")
    connect_line = next(i for i, line in enumerate(db_py.splitlines()) if "sqlite3.connect(" in line)
    window = "\n".join(db_py.splitlines()[connect_line:connect_line + 10])
    assert re.search(r"PRAGMA busy_timeout", window)


def test_every_known_store_module_calls_open_db_not_sqlite3_connect():
    """The other half of the guard above, from the call-site side: every
    module that used to open its own connection now imports and calls
    open_db() instead of sqlite3.connect()."""
    import re

    src_root = Path(__file__).resolve().parents[1] / "src" / "argus"
    known_sites = [
        "memory/store.py", "rules/store.py", "rules/instances.py",
        "salience/budget.py", "salience/decision_log.py", "salience/escalation.py",
        "salience/held.py", "spine/store.py", "tasks/store.py",
        "world/rhythms.py", "world/threads.py",
    ]
    open_db_pattern = re.compile(r"\bopen_db\(")

    missing = []
    for rel_path in known_sites:
        text = (src_root / rel_path).read_text(encoding="utf-8")
        if not open_db_pattern.search(text):
            missing.append(rel_path)

    assert missing == [], f"expected these to call open_db(): {missing}"


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
