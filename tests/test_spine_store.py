import pathlib
import threading
import time

from argus.spine.observation import Observation
from argus.spine.store import SpineStore


def _obs(**overrides) -> Observation:
    defaults = dict(
        source="test_sensor", kind="focus.changed", ts=time.time(),
        subject="some window", payload={"a": 1, "b": "two"}, confidence=0.75,
        dedupe_key=None,
    )
    defaults.update(overrides)
    return Observation(**defaults)


def test_record_and_query_round_trips_every_field(tmp_path):
    store = SpineStore(tmp_path / "spine.db")
    obs = _obs(dedupe_key="unique-1")

    row_id = store.record(obs)
    assert row_id is not None

    (back,) = store.query(limit=10)
    assert back.source == obs.source
    assert back.kind == obs.kind
    assert back.ts == obs.ts
    assert back.subject == obs.subject
    assert back.payload == obs.payload
    assert back.confidence == obs.confidence
    assert back.dedupe_key == obs.dedupe_key


def test_duplicate_dedupe_key_inserts_one_row(tmp_path):
    store = SpineStore(tmp_path / "spine.db")
    obs = _obs(dedupe_key="mail:Gmail:123")

    first_id = store.record(obs)
    second_id = store.record(obs)

    assert first_id is not None
    assert second_id is None
    assert store.count() == 1


def test_none_dedupe_key_does_not_collide(tmp_path):
    store = SpineStore(tmp_path / "spine.db")
    store.record(_obs(dedupe_key=None))
    store.record(_obs(dedupe_key=None))
    assert store.count() == 2


def test_concurrent_writes_do_not_raise_database_is_locked(tmp_path):
    store = SpineStore(tmp_path / "spine.db")
    errors = []

    def _write(n: int) -> None:
        try:
            for i in range(25):
                store.record(_obs(dedupe_key=f"thread-{n}-{i}"))
        except Exception as e:  # pragma: no cover - failure path
            errors.append(e)

    threads = [threading.Thread(target=_write, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert store.count() == 6 * 25


def test_observations_persist_across_restart(tmp_path):
    db_path = tmp_path / "spine.db"
    store = SpineStore(db_path)
    store.record(_obs(dedupe_key="persisted-1"))

    reopened = SpineStore(db_path)
    assert reopened.count() == 1
    assert reopened.query(limit=1)[0].dedupe_key == "persisted-1"


def test_query_orders_newest_first(tmp_path):
    store = SpineStore(tmp_path / "spine.db")
    store.record(_obs(ts=100.0, dedupe_key="a"))
    store.record(_obs(ts=300.0, dedupe_key="b"))
    store.record(_obs(ts=200.0, dedupe_key="c"))

    results = store.query(limit=10)
    assert [o.ts for o in results] == [300.0, 200.0, 100.0]


def test_query_filters_by_kind_and_since(tmp_path):
    store = SpineStore(tmp_path / "spine.db")
    store.record(_obs(kind="mail.received", ts=100.0, dedupe_key="a"))
    store.record(_obs(kind="focus.changed", ts=200.0, dedupe_key="b"))
    store.record(_obs(kind="mail.received", ts=300.0, dedupe_key="c"))

    results = store.query(kinds=["mail.received"], since=150.0)
    assert len(results) == 1
    assert results[0].dedupe_key == "c"


def test_query_ts_subject_matches_query_filters_but_returns_lightweight_tuples(tmp_path):
    store = SpineStore(tmp_path / "spine.db")
    store.record(_obs(kind="mail.received", ts=100.0, subject="a@x.com", dedupe_key="a"))
    store.record(_obs(kind="focus.changed", ts=200.0, subject="VS Code", dedupe_key="b"))
    store.record(_obs(kind="mail.received", ts=300.0, subject="b@x.com", dedupe_key="c"))

    results = store.query_ts_subject(kinds=["mail.received"], since=150.0)

    assert results == [(300.0, "b@x.com")]


def test_latest_returns_most_recent_of_a_kind(tmp_path):
    store = SpineStore(tmp_path / "spine.db")
    store.record(_obs(kind="mail.received", subject="a@x.com", ts=100.0, dedupe_key="a"))
    store.record(_obs(kind="mail.received", subject="a@x.com", ts=200.0, dedupe_key="b"))

    latest = store.latest("mail.received", subject="a@x.com")
    assert latest.dedupe_key == "b"
    assert store.latest("git.commit") is None


def test_earliest_ts_returns_none_when_empty(tmp_path):
    store = SpineStore(tmp_path / "spine.db")
    assert store.earliest_ts() is None


def test_earliest_ts_returns_the_oldest_observation_regardless_of_insertion_order(tmp_path):
    store = SpineStore(tmp_path / "spine.db")
    store.record(_obs(ts=200.0, dedupe_key="b"))
    store.record(_obs(ts=100.0, dedupe_key="a"))
    store.record(_obs(ts=300.0, dedupe_key="c"))

    assert store.earliest_ts() == 100.0


def test_prune_removes_only_older_than_cutoff(tmp_path):
    store = SpineStore(tmp_path / "spine.db")
    now = time.time()
    store.record(_obs(ts=now - 500 * 86400, dedupe_key="old"))
    store.record(_obs(ts=now, dedupe_key="new"))

    removed = store.prune(older_than_days=400)

    assert removed == 1
    assert store.count() == 1
    assert store.query(limit=10)[0].dedupe_key == "new"


def test_record_returns_none_rather_than_raising_when_db_unwritable(tmp_path):
    import sqlite3

    class _BoomConnection:
        def execute(self, *a, **k):
            raise sqlite3.OperationalError("disk I/O error")

    store = SpineStore(tmp_path / "spine.db")
    store._conn = _BoomConnection()

    assert store.record(_obs(dedupe_key="whatever")) is None


def test_record_never_raises_on_an_unserializable_payload(tmp_path):
    """Found at the Phase A gate review: record() promised it never raises
    to its caller, but json.dumps on a payload holding a datetime/Path/set
    raised TypeError straight through -- past the `except sqlite3.Error`,
    which doesn't cover it. Sensor.run() would have caught it, but the rest
    of that poll's observations were lost and the traceback pointed at the
    sensor rather than the payload; and every non-sensor caller added from
    Phase B onward has no such catch at all. Encodes the mechanism (a
    non-JSON type in the payload), not just the symptom."""
    import datetime

    store = SpineStore(tmp_path / "spine.db")
    obs = Observation(
        source="probe", kind="focus.changed", ts=1.0,
        payload={"when": datetime.datetime(2026, 9, 1), "where": pathlib.Path("C:/x"), "tags": {"a"}},
    )

    row_id = store.record(obs)

    assert row_id is not None, "the observation must still be recorded, not dropped"
    stored = store.query(kinds=["focus.changed"])[0]
    # Lossy-but-present beats absent: the values survive as strings.
    assert "2026-09-01" in stored.payload["when"]
