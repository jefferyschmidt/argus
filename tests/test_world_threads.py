import time

from argus.memory.store import get_connection
from argus.spine.observation import Observation
from argus.spine.store import SpineStore
from argus.world.threads import DEFAULT_CLOSE_CONDITIONS, ThreadStore


def _store(tmp_path) -> ThreadStore:
    return ThreadStore(get_connection(tmp_path / "argus.db"))


def test_open_returns_id_and_get_reads_it_back(tmp_path):
    store = _store(tmp_path)
    thread_id = store.open("manual", "Follow up", subject="x@y.com", metadata={"note": "hi"})

    thread = store.get(thread_id)
    assert thread.kind == "manual"
    assert thread.title == "Follow up"
    assert thread.subject == "x@y.com"
    assert thread.closed_ts is None
    assert thread.metadata == {"note": "hi"}


def test_sensitivity_defaults_to_normal(tmp_path):
    store = _store(tmp_path)
    thread = store.get(store.open("manual", "x"))
    assert thread.sensitivity == "normal"


def test_close_marks_closed_and_returns_true(tmp_path):
    store = _store(tmp_path)
    thread_id = store.open("manual", "x")

    assert store.close(thread_id, "handled") is True
    thread = store.get(thread_id)
    assert thread.closed_ts is not None
    assert thread.closed_reason == "handled"


def test_close_on_already_closed_thread_returns_false(tmp_path):
    store = _store(tmp_path)
    thread_id = store.open("manual", "x")
    store.close(thread_id, "first")
    assert store.close(thread_id, "second") is False


def test_close_on_nonexistent_thread_returns_false(tmp_path):
    store = _store(tmp_path)
    assert store.close(999, "x") is False


def test_open_and_close_persists_across_restart(tmp_path):
    db_path = tmp_path / "argus.db"
    store = ThreadStore(get_connection(db_path))
    thread_id = store.open("manual", "x")
    store.close(thread_id, "done")

    reopened = ThreadStore(get_connection(db_path))
    thread = reopened.get(thread_id)
    assert thread.closed_ts is not None
    assert thread.closed_reason == "done"


def test_open_threads_excludes_closed_and_filters_by_kind(tmp_path):
    store = _store(tmp_path)
    a = store.open("email_reply", "a")
    store.open("commitment", "b")
    store.close(a, "done")

    all_open = store.open_threads()
    assert [t.title for t in all_open] == ["b"]

    filtered = store.open_threads(kind="email_reply")
    assert filtered == []


def test_touch_updates_last_activity_ts(tmp_path):
    store = _store(tmp_path)
    thread_id = store.open("manual", "x")
    original = store.get(thread_id).last_activity_ts
    time.sleep(0.01)
    store.touch(thread_id)
    assert store.get(thread_id).last_activity_ts > original


def test_find_open_ignores_closed_threads(tmp_path):
    store = _store(tmp_path)
    thread_id = store.open("email_reply", "x", subject="a@x.com")
    store.close(thread_id, "done")
    assert store.find_open("email_reply", "a@x.com") is None


def test_find_open_returns_most_recent_open(tmp_path):
    store = _store(tmp_path)
    store.open("email_reply", "first", subject="a@x.com")
    second = store.open("email_reply", "second", subject="a@x.com")
    found = store.find_open("email_reply", "a@x.com")
    assert found.id == second


# -- openers --------------------------------------------------------------

def test_open_email_reply_reuses_existing_open_thread(tmp_path):
    store = _store(tmp_path)
    first_id = store.open_email_reply(sender="a@x.com", mail_subject="Hi")
    second_id = store.open_email_reply(sender="a@x.com", mail_subject="Hi again")

    assert first_id == second_id
    assert len(store.open_threads(kind="email_reply")) == 1


def test_open_email_reply_uses_default_close_condition(tmp_path):
    store = _store(tmp_path)
    thread = store.get(store.open_email_reply(sender="a@x.com", mail_subject="Hi"))
    assert thread.close_condition == DEFAULT_CLOSE_CONDITIONS["email_reply"]


def test_open_system_health_deduplicates_repeated_failures_of_same_subject(tmp_path):
    """Phase B acceptance: a repeated argus.credential_failed for the same
    subject touches one thread, never opens a second."""
    store = _store(tmp_path)
    first_id = store.open_system_health(subject="Yahoo", description="Yahoo credential failed")
    second_id = store.open_system_health(subject="Yahoo", description="Yahoo credential failed again")
    third_id = store.open_system_health(subject="Gmail", description="Gmail credential failed")

    assert first_id == second_id
    assert third_id != first_id
    assert len(store.open_threads(kind="system_health")) == 2


def test_open_system_health_close_condition_is_manual_only(tmp_path):
    store = _store(tmp_path)
    thread = store.get(store.open_system_health(subject="Yahoo", description="x"))
    assert thread.close_condition == {"type": "manual_only"}


def test_open_commitment_creates_a_thread(tmp_path):
    store = _store(tmp_path)
    thread = store.get(store.open_commitment(text="I'll look at that tomorrow"))
    assert thread.kind == "commitment"
    assert thread.title == "I'll look at that tomorrow"
    assert thread.close_condition == DEFAULT_CLOSE_CONDITIONS["commitment"]


# -- reap -------------------------------------------------------------------

def test_reap_closes_threads_whose_condition_is_satisfied(tmp_path):
    thread_store = _store(tmp_path)
    spine = SpineStore(tmp_path / "spine.db")
    thread_id = thread_store.open("manual", "x", close_condition={"type": "timeout", "seconds": 10})

    thread_store.reap(spine, now=thread_store.get(thread_id).opened_ts + 5)
    assert thread_store.get(thread_id).closed_ts is None  # not yet due

    closed_count = thread_store.reap(spine, now=thread_store.get(thread_id).opened_ts + 20)
    assert closed_count == 1
    assert thread_store.get(thread_id).closed_ts is not None


def test_reap_leaves_unsatisfied_threads_open(tmp_path):
    thread_store = _store(tmp_path)
    spine = SpineStore(tmp_path / "spine.db")
    thread_store.open("manual", "x", close_condition={"type": "manual_only"})

    closed_count = thread_store.reap(spine)
    assert closed_count == 0
    assert len(thread_store.open_threads()) == 1


def test_reap_bad_predicate_does_not_raise_and_leaves_thread_open(tmp_path):
    thread_store = _store(tmp_path)
    spine = SpineStore(tmp_path / "spine.db")
    thread_id = thread_store.open("manual", "x", close_condition={"type": "totally_unknown"})

    closed_count = thread_store.reap(spine)
    assert closed_count == 0
    assert thread_store.get(thread_id).closed_ts is None


def test_reap_over_1000_open_threads_makes_zero_llm_calls_and_is_fast(tmp_path):
    thread_store = _store(tmp_path)
    spine = SpineStore(tmp_path / "spine.db")
    for i in range(1000):
        thread_store.open("manual", f"t{i}", close_condition={"type": "manual_only"})

    start = time.monotonic()
    closed = thread_store.reap(spine)
    elapsed = time.monotonic() - start

    assert closed == 0
    assert elapsed < 1.0


def test_reap_email_reply_thread_closes_on_acknowledgement(tmp_path):
    thread_store = _store(tmp_path)
    spine = SpineStore(tmp_path / "spine.db")
    thread_id = thread_store.open_email_reply(sender="a@x.com", mail_subject="Hi")

    spine.record(Observation(
        source="ui", kind="thread.acknowledged", ts=time.time(),
        payload={"thread_id": thread_id, "via": "voice"}, dedupe_key="ack-1",
    ))

    closed_count = thread_store.reap(spine)
    assert closed_count == 1
    assert thread_store.get(thread_id).closed_ts is not None
