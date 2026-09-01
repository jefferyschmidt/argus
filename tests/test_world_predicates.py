import logging
import time
from dataclasses import dataclass

from argus.spine.observation import Observation
from argus.spine.store import SpineStore
from argus.world.predicates import evaluate


@dataclass
class _FakeThread:
    id: int
    subject: str | None
    opened_ts: float


def _store(tmp_path) -> SpineStore:
    return SpineStore(tmp_path / "spine.db")


# -- unknown type -------------------------------------------------------

def test_unknown_type_returns_false_and_logs(tmp_path, caplog):
    store = _store(tmp_path)
    with caplog.at_level(logging.WARNING):
        result = evaluate({"type": "not_a_real_type"}, thread=None, spine=store, now=time.time())
    assert result is False
    assert "Unknown predicate type" in caplog.text


def test_missing_type_returns_false_without_raising(tmp_path):
    store = _store(tmp_path)
    assert evaluate({}, thread=None, spine=store, now=time.time()) is False


# -- 1. observation_seen --------------------------------------------------

def test_observation_seen_passes_when_matching_observation_recorded(tmp_path):
    store = _store(tmp_path)
    thread = _FakeThread(id=1, subject="a@x.com", opened_ts=100.0)
    store.record(Observation(source="mail", kind="mail.replied", ts=200.0, subject="a@x.com", dedupe_key="a"))

    predicate = {"type": "observation_seen", "kind": "mail.replied", "subject": "$thread.subject", "since": "$thread.opened_ts"}
    assert evaluate(predicate, thread=thread, spine=store, now=300.0) is True


def test_observation_seen_fails_when_no_matching_observation(tmp_path):
    store = _store(tmp_path)
    thread = _FakeThread(id=1, subject="a@x.com", opened_ts=100.0)

    predicate = {"type": "observation_seen", "kind": "mail.replied", "subject": "$thread.subject"}
    assert evaluate(predicate, thread=thread, spine=store, now=300.0) is False


def test_observation_seen_binds_thread_subject_and_opened_ts(tmp_path):
    store = _store(tmp_path)
    thread = _FakeThread(id=1, subject="b@x.com", opened_ts=250.0)
    # Recorded before the thread opened -- must not satisfy `since`.
    store.record(Observation(source="mail", kind="mail.replied", ts=200.0, subject="b@x.com", dedupe_key="early"))
    predicate = {"type": "observation_seen", "kind": "mail.replied", "subject": "$thread.subject", "since": "$thread.opened_ts"}

    assert evaluate(predicate, thread=thread, spine=store, now=300.0) is False

    store.record(Observation(source="mail", kind="mail.replied", ts=260.0, subject="b@x.com", dedupe_key="late"))
    assert evaluate(predicate, thread=thread, spine=store, now=300.0) is True


def test_observation_seen_without_subject_matches_any_subject(tmp_path):
    store = _store(tmp_path)
    store.record(Observation(source="mail", kind="mail.deleted", ts=200.0, subject="anyone@x.com", dedupe_key="a"))
    predicate = {"type": "observation_seen", "kind": "mail.deleted"}
    assert evaluate(predicate, thread=None, spine=store, now=300.0) is True


# -- 2. user_acknowledged --------------------------------------------------

def test_user_acknowledged_passes_when_ack_observation_matches_thread(tmp_path):
    store = _store(tmp_path)
    thread = _FakeThread(id=42, subject=None, opened_ts=100.0)
    store.record(Observation(source="ui", kind="thread.acknowledged", ts=200.0, payload={"thread_id": 42, "via": "voice"}, dedupe_key="a"))

    assert evaluate({"type": "user_acknowledged"}, thread=thread, spine=store, now=300.0) is True


def test_user_acknowledged_fails_for_a_different_thread_id(tmp_path):
    store = _store(tmp_path)
    thread = _FakeThread(id=42, subject=None, opened_ts=100.0)
    store.record(Observation(source="ui", kind="thread.acknowledged", ts=200.0, payload={"thread_id": 99, "via": "voice"}, dedupe_key="a"))

    assert evaluate({"type": "user_acknowledged"}, thread=thread, spine=store, now=300.0) is False


def test_user_acknowledged_with_no_thread_is_false(tmp_path):
    store = _store(tmp_path)
    assert evaluate({"type": "user_acknowledged"}, thread=None, spine=store, now=300.0) is False


# -- 3. timeout --------------------------------------------------------

def test_timeout_passes_once_elapsed(tmp_path):
    store = _store(tmp_path)
    thread = _FakeThread(id=1, subject=None, opened_ts=100.0)
    predicate = {"type": "timeout", "seconds": 100.0}
    assert evaluate(predicate, thread=thread, spine=store, now=250.0) is True


def test_timeout_fails_before_elapsed(tmp_path):
    store = _store(tmp_path)
    thread = _FakeThread(id=1, subject=None, opened_ts=100.0)
    predicate = {"type": "timeout", "seconds": 100.0}
    assert evaluate(predicate, thread=thread, spine=store, now=150.0) is False


# -- 4. manual_only --------------------------------------------------------

def test_manual_only_is_always_false(tmp_path):
    store = _store(tmp_path)
    thread = _FakeThread(id=1, subject=None, opened_ts=0.0)
    # Even with a very long elapsed time, manual_only never self-satisfies --
    # only an explicit ThreadStore.close() call closes it.
    assert evaluate({"type": "manual_only"}, thread=thread, spine=store, now=10**9) is False
    assert evaluate({"type": "manual_only"}, thread=None, spine=store, now=0.0) is False


# -- 5. thread_closed --------------------------------------------------------

def test_thread_closed_passes_when_target_thread_closed_observation_exists(tmp_path):
    store = _store(tmp_path)
    store.record(Observation(source="world", kind="thread.closed", ts=200.0, payload={"thread_id": 7}, dedupe_key="a"))
    assert evaluate({"type": "thread_closed", "thread_id": 7}, thread=None, spine=store, now=300.0) is True


def test_thread_closed_fails_when_no_matching_observation(tmp_path):
    store = _store(tmp_path)
    assert evaluate({"type": "thread_closed", "thread_id": 7}, thread=None, spine=store, now=300.0) is False


# -- 6. value_threshold --------------------------------------------------------

def test_value_threshold_passes_when_comparison_holds(tmp_path):
    store = _store(tmp_path)
    store.record(Observation(source="argus.health", kind="argus.spend_recorded", ts=200.0, payload={"usd": 75.0}, dedupe_key="a"))
    predicate = {"type": "value_threshold", "kind": "argus.spend_recorded", "field": "payload.usd", "op": "gt", "value": 50.0}
    assert evaluate(predicate, thread=None, spine=store, now=300.0) is True


def test_value_threshold_fails_when_comparison_does_not_hold(tmp_path):
    store = _store(tmp_path)
    store.record(Observation(source="argus.health", kind="argus.spend_recorded", ts=200.0, payload={"usd": 10.0}, dedupe_key="a"))
    predicate = {"type": "value_threshold", "kind": "argus.spend_recorded", "field": "payload.usd", "op": "gt", "value": 50.0}
    assert evaluate(predicate, thread=None, spine=store, now=300.0) is False


def test_value_threshold_unknown_op_is_false_and_logs(tmp_path, caplog):
    store = _store(tmp_path)
    store.record(Observation(source="argus.health", kind="argus.spend_recorded", ts=200.0, payload={"usd": 75.0}, dedupe_key="a"))
    predicate = {"type": "value_threshold", "kind": "argus.spend_recorded", "field": "payload.usd", "op": "between", "value": 50.0}
    with caplog.at_level(logging.WARNING):
        assert evaluate(predicate, thread=None, spine=store, now=300.0) is False
    assert "Unknown value_threshold op" in caplog.text


def test_value_threshold_no_observation_of_kind_is_false(tmp_path):
    store = _store(tmp_path)
    predicate = {"type": "value_threshold", "kind": "argus.spend_recorded", "field": "payload.usd", "op": "gt", "value": 50.0}
    assert evaluate(predicate, thread=None, spine=store, now=300.0) is False


# -- 7. any_of / all_of --------------------------------------------------------

def test_any_of_passes_if_one_predicate_matches(tmp_path):
    store = _store(tmp_path)
    predicate = {"type": "any_of", "predicates": [{"type": "manual_only"}, {"type": "timeout", "seconds": 0}]}
    thread = _FakeThread(id=1, subject=None, opened_ts=0.0)
    assert evaluate(predicate, thread=thread, spine=store, now=10.0) is True


def test_any_of_fails_if_no_predicate_matches(tmp_path):
    store = _store(tmp_path)
    predicate = {"type": "any_of", "predicates": [{"type": "manual_only"}, {"type": "timeout", "seconds": 1000}]}
    thread = _FakeThread(id=1, subject=None, opened_ts=0.0)
    assert evaluate(predicate, thread=thread, spine=store, now=10.0) is False


def test_all_of_passes_only_if_every_predicate_matches(tmp_path):
    store = _store(tmp_path)
    thread = _FakeThread(id=1, subject=None, opened_ts=0.0)
    predicate = {"type": "all_of", "predicates": [{"type": "timeout", "seconds": 0}, {"type": "timeout", "seconds": 5}]}
    assert evaluate(predicate, thread=thread, spine=store, now=10.0) is True


def test_all_of_fails_if_one_predicate_does_not_match(tmp_path):
    store = _store(tmp_path)
    thread = _FakeThread(id=1, subject=None, opened_ts=0.0)
    predicate = {"type": "all_of", "predicates": [{"type": "timeout", "seconds": 0}, {"type": "timeout", "seconds": 1000}]}
    assert evaluate(predicate, thread=thread, spine=store, now=10.0) is False


def test_any_of_and_all_of_nest_three_deep(tmp_path):
    store = _store(tmp_path)
    thread = _FakeThread(id=1, subject=None, opened_ts=0.0)
    predicate = {
        "type": "all_of",
        "predicates": [
            {"type": "any_of", "predicates": [
                {"type": "all_of", "predicates": [
                    {"type": "timeout", "seconds": 0},
                    {"type": "timeout", "seconds": 5},
                ]},
                {"type": "manual_only"},
            ]},
            {"type": "timeout", "seconds": 0},
        ],
    }
    assert evaluate(predicate, thread=thread, spine=store, now=10.0) is True


def test_predicates_field_not_a_list_is_false(tmp_path):
    store = _store(tmp_path)
    assert evaluate({"type": "any_of", "predicates": "not-a-list"}, thread=None, spine=store, now=0.0) is False
    assert evaluate({"type": "all_of", "predicates": "not-a-list"}, thread=None, spine=store, now=0.0) is False
