import time

import pytest

from argus.cli import _parse_since, timeline
from argus.spine.observation import Observation
from argus.spine.store import SpineStore


def test_parse_since_hours():
    now = time.time()
    ts = _parse_since("24h")
    assert abs((now - ts) - 24 * 3600) < 2


def test_parse_since_minutes_and_days():
    now = time.time()
    assert abs((now - _parse_since("30m")) - 30 * 60) < 2
    assert abs((now - _parse_since("7d")) - 7 * 86400) < 2


def test_parse_since_invalid_raises():
    with pytest.raises(ValueError):
        _parse_since("not-a-duration")


def _store(tmp_path, monkeypatch) -> SpineStore:
    store = SpineStore(tmp_path / "spine.db")
    # timeline() imports SpineStore locally (`from argus.spine.store import
    # SpineStore`), so the patch target is the source module's attribute,
    # not argus.cli's namespace.
    monkeypatch.setattr("argus.spine.store.SpineStore", lambda: store)
    return store


def test_timeline_prints_recent_observations_newest_first(tmp_path, monkeypatch, capsys):
    store = _store(tmp_path, monkeypatch)
    store.record(Observation(source="mail", kind="mail.received", ts=100.0, subject="a@x.com", dedupe_key="a"))
    store.record(Observation(source="mail", kind="mail.received", ts=200.0, subject="b@x.com", dedupe_key="b"))

    timeline(since=None, kind=None, limit=50)

    out = capsys.readouterr().out
    assert out.index("b@x.com") < out.index("a@x.com")


def test_timeline_with_no_observations_prints_a_message(tmp_path, monkeypatch, capsys):
    _store(tmp_path, monkeypatch)

    timeline(since=None, kind=None, limit=50)

    assert "No observations recorded yet." in capsys.readouterr().out


def test_timeline_filters_by_kind(tmp_path, monkeypatch, capsys):
    store = _store(tmp_path, monkeypatch)
    store.record(Observation(source="mail", kind="mail.received", ts=100.0, dedupe_key="a"))
    store.record(Observation(source="git", kind="git.commit", ts=200.0, dedupe_key="b"))

    timeline(since=None, kind="git.commit", limit=50)

    out = capsys.readouterr().out
    assert "git.commit" in out
    assert "mail.received" not in out


def test_timeline_invalid_since_prints_error_and_does_not_raise(tmp_path, monkeypatch, capsys):
    _store(tmp_path, monkeypatch)

    timeline(since="bogus", kind=None, limit=50)

    assert "Invalid --since" in capsys.readouterr().out
