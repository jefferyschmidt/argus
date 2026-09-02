import time

from argus.spine.observation import Observation
from argus.spine.store import SpineStore
from argus.tools.base import PermissionTier
from argus.tools.query_timeline import _MAX_LIMIT, _build_query_timeline


def _obs(**overrides) -> Observation:
    defaults = dict(
        source="test_sensor", kind="mail.received", ts=time.time(),
        subject="a@x.com", payload={}, confidence=1.0, dedupe_key=None,
    )
    defaults.update(overrides)
    return Observation(**defaults)


def _store(tmp_path) -> SpineStore:
    return SpineStore(tmp_path / "spine.db")


def test_query_timeline_is_allow_tier(tmp_path):
    assert _build_query_timeline(_store(tmp_path)).tier == PermissionTier.ALLOW


def test_query_timeline_with_no_observations(tmp_path):
    tool = _build_query_timeline(_store(tmp_path))
    assert tool.handler({}) == "No matching observations."


def test_query_timeline_answers_when_did_i_last_get_mail_from_x(tmp_path):
    store = _store(tmp_path)
    now = time.time()
    store.record(_obs(kind="mail.received", subject="julia@x.com", ts=now - 3600, dedupe_key="1"))
    store.record(_obs(kind="mail.received", subject="other@y.com", ts=now - 60, dedupe_key="2"))
    tool = _build_query_timeline(store)

    result = tool.handler({"kinds": ["mail.received"], "subject": "julia@x.com"})

    assert "mail.received: 1" in result
    assert "julia@x.com" in result
    assert "other@y.com" not in result


def test_query_timeline_summarizes_counts_by_kind_not_a_raw_dump(tmp_path):
    store = _store(tmp_path)
    now = time.time()
    for i in range(3):
        store.record(_obs(kind="mail.received", ts=now - i, dedupe_key=f"mail-{i}"))
    for i in range(2):
        store.record(_obs(kind="git.commit", subject="argus", ts=now - i, dedupe_key=f"git-{i}"))
    tool = _build_query_timeline(store)

    result = tool.handler({})

    assert "mail.received: 3" in result
    assert "git.commit: 2" in result
    assert "5 observation(s)" in result


def test_query_timeline_shows_only_the_most_recent_handful(tmp_path):
    store = _store(tmp_path)
    now = time.time()
    for i in range(20):
        store.record(_obs(kind="git.commit", ts=now - i, dedupe_key=f"c{i}"))
    tool = _build_query_timeline(store)

    result = tool.handler({})

    # 20 observations counted, but only a handful of individual rows listed.
    assert "20 observation(s)" in result
    assert result.count("git.commit") <= 6  # the counts line plus <=5 recent rows


def test_query_timeline_window_predating_the_spine_says_so_honestly(tmp_path):
    """ROADMAP.md P1: honest uncertainty -- a query whose window starts
    before the earliest observation must say the record doesn't go back
    that far, not just silently report nothing."""
    store = _store(tmp_path)
    now = time.time()
    store.record(_obs(kind="mail.received", ts=now - 3600, dedupe_key="1"))  # 1 hour ago
    tool = _build_query_timeline(store)

    result = tool.handler({"since_hours_ago": 24})  # asks for the last 24 hours

    assert "only goes back to" in result


def test_query_timeline_within_recorded_range_gives_no_honesty_caveat(tmp_path):
    store = _store(tmp_path)
    now = time.time()
    store.record(_obs(kind="mail.received", ts=now - 3600 * 48, dedupe_key="1"))  # 48 hours ago
    tool = _build_query_timeline(store)

    result = tool.handler({"since_hours_ago": 24})  # window is well within the recorded range

    assert "only goes back to" not in result


def test_query_timeline_limit_is_capped_server_side_regardless_of_request(tmp_path):
    store = _store(tmp_path)
    now = time.time()
    for i in range(10):
        store.record(_obs(kind="git.commit", ts=now - i, dedupe_key=f"c{i}"))
    tool = _build_query_timeline(store)

    result = tool.handler({"limit": 999999})

    assert "10 observation(s)" in result  # only 10 exist -- proves the cap didn't error, not that it clamped


def test_query_timeline_limit_actually_clamps_at_the_max(tmp_path, monkeypatch):
    store = _store(tmp_path)
    calls = {}
    real_query = store.query

    def spying_query(**kwargs):
        calls["limit"] = kwargs["limit"]
        return real_query(**kwargs)

    monkeypatch.setattr(store, "query", spying_query)
    tool = _build_query_timeline(store)

    tool.handler({"limit": 999999})

    assert calls["limit"] == _MAX_LIMIT


def test_query_timeline_default_limit_is_fifty(tmp_path, monkeypatch):
    store = _store(tmp_path)
    calls = {}
    real_query = store.query

    def spying_query(**kwargs):
        calls["limit"] = kwargs["limit"]
        return real_query(**kwargs)

    monkeypatch.setattr(store, "query", spying_query)
    tool = _build_query_timeline(store)

    tool.handler({})

    assert calls["limit"] == 50
