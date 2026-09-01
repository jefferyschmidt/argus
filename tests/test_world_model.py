import time
from unittest.mock import patch

from argus.memory.store import get_connection
from argus.spine.observation import Observation
from argus.spine.store import SpineStore
from argus.world.model import WorldModel
from argus.world.rhythms import RhythmStore
from argus.world.threads import ThreadStore


def _model(tmp_path) -> WorldModel:
    conn = get_connection(tmp_path / "argus.db")
    spine = SpineStore(tmp_path / "spine.db")
    return WorldModel(spine=spine, threads=ThreadStore(spine, tmp_path / "argus.db"), rhythms=RhythmStore(conn))


# -- focus ------------------------------------------------------------------

def test_snapshot_has_no_focus_when_nothing_observed(tmp_path):
    snap = _model(tmp_path).snapshot()
    assert snap.focus is None


def test_snapshot_focus_reflects_latest_focus_changed(tmp_path):
    model = _model(tmp_path)
    model.spine.record(Observation(source="window_focus", kind="focus.changed", ts=time.time() - 600, subject="VS Code", dedupe_key="a"))

    snap = model.snapshot()
    assert snap.focus.title == "VS Code"
    assert snap.focus.minutes >= 9.9  # ~10 minutes ago


# -- open_threads / health ---------------------------------------------------

def test_snapshot_includes_open_threads_and_excludes_closed(tmp_path):
    model = _model(tmp_path)
    keep = model.threads.open("email_reply", "Reply to a@x.com")
    drop = model.threads.open("commitment", "will close this")
    model.threads.close(drop, "done")

    snap = model.snapshot()
    titles = [t.title for t in snap.open_threads]
    assert "Reply to a@x.com" in titles
    assert "will close this" not in titles
    assert keep  # sanity: id was assigned


def test_snapshot_health_issues_come_from_open_system_health_threads(tmp_path):
    model = _model(tmp_path)
    model.threads.open_system_health(subject="Yahoo", description="Yahoo credential failed")

    snap = model.snapshot()
    assert len(snap.health) == 1
    assert snap.health[0].subject == "Yahoo"
    assert snap.health[0].description == "Yahoo credential failed"


# -- calendar horizon ---------------------------------------------------------

def test_calendar_horizon_excludes_events_beyond_horizon_hours(tmp_path, monkeypatch):
    monkeypatch.setattr("argus.world.model.settings.horizon_hours", 12.0)
    model = _model(tmp_path)
    from datetime import datetime, timedelta, timezone

    soon = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    far = (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat()
    model.spine.record(Observation(source="calendar", kind="calendar.event_upcoming", ts=time.time(),
                                    subject="Standup", payload={"id": "1", "summary": "Standup", "start": soon}, dedupe_key="a"))
    model.spine.record(Observation(source="calendar", kind="calendar.event_upcoming", ts=time.time(),
                                    subject="Quarterly review", payload={"id": "2", "summary": "Quarterly review", "start": far}, dedupe_key="b"))

    snap = model.snapshot()
    summaries = [c.summary for c in snap.horizon]
    assert "Standup" in summaries
    assert "Quarterly review" not in summaries


def test_calendar_horizon_dedupes_rescheduled_events_by_id(tmp_path):
    model = _model(tmp_path)
    from datetime import datetime, timedelta, timezone

    original_start = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    new_start = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    model.spine.record(Observation(source="calendar", kind="calendar.event_upcoming", ts=time.time() - 100,
                                    payload={"id": "1", "summary": "Standup", "start": original_start}, dedupe_key="a"))
    model.spine.record(Observation(source="calendar", kind="calendar.event_upcoming", ts=time.time(),
                                    payload={"id": "1", "summary": "Standup", "start": new_start}, dedupe_key="b"))

    snap = model.snapshot()
    matching = [c for c in snap.horizon if c.summary == "Standup"]
    assert len(matching) == 1
    assert matching[0].start == new_start


def test_calendar_horizon_carries_end_time(tmp_path):
    model = _model(tmp_path)
    from datetime import datetime, timedelta, timezone

    start = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    end = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    model.spine.record(Observation(
        source="calendar", kind="calendar.event_upcoming", ts=time.time(),
        payload={"id": "1", "summary": "Standup", "start": start, "end": end}, dedupe_key="a",
    ))

    snap = model.snapshot()
    assert snap.horizon[0].end == end


# -- rhythms ------------------------------------------------------------------

def test_snapshot_rhythm_summary_reflects_recomputed_confidence(tmp_path, monkeypatch):
    monkeypatch.setattr("argus.world.rhythms.settings.rhythm_min_days", 1)
    model = _model(tmp_path)
    model.spine.record(Observation(source="mail", kind="mail.received", ts=time.time(), subject="a@x.com", dedupe_key="a"))
    model.rhythms.recompute(model.spine)

    snap = model.snapshot()
    assert "active_hours" in snap.rhythms.baselines


# -- caching / TTL ---------------------------------------------------------

def test_snapshot_is_cached_within_ttl(tmp_path, monkeypatch):
    monkeypatch.setattr("argus.world.model.settings.world_snapshot_ttl_seconds", 5.0)
    model = _model(tmp_path)

    with patch.object(model, "_build_snapshot", wraps=model._build_snapshot) as build:
        for _ in range(100):
            model.snapshot()
        assert build.call_count == 1


def test_snapshot_recomputes_after_ttl_expires(tmp_path, monkeypatch):
    monkeypatch.setattr("argus.world.model.settings.world_snapshot_ttl_seconds", 0.01)
    model = _model(tmp_path)

    with patch.object(model, "_build_snapshot", wraps=model._build_snapshot) as build:
        model.snapshot()
        time.sleep(0.05)
        model.snapshot()
        assert build.call_count == 2


def test_open_thread_and_close_thread_invalidate_the_cache(tmp_path, monkeypatch):
    monkeypatch.setattr("argus.world.model.settings.world_snapshot_ttl_seconds", 5.0)
    model = _model(tmp_path)

    with patch.object(model, "_build_snapshot", wraps=model._build_snapshot) as build:
        model.snapshot()
        thread_id = model.open_thread("manual", "x")
        model.snapshot()
        model.close_thread(thread_id, "done")
        model.snapshot()
        assert build.call_count == 3


# -- to_prompt_block ---------------------------------------------------------

def test_to_prompt_block_never_exceeds_max_chars_with_500_open_threads(tmp_path):
    model = _model(tmp_path)
    for i in range(500):
        model.threads.open("manual", f"Thread number {i} with a fairly long descriptive title")

    snap = model.snapshot()
    block = snap.to_prompt_block(max_chars=1200)
    assert len(block) <= 1200


def test_to_prompt_block_respects_a_small_max_chars(tmp_path):
    model = _model(tmp_path)
    model.threads.open("manual", "x" * 2000)

    snap = model.snapshot()
    block = snap.to_prompt_block(max_chars=50)
    assert len(block) <= 50


def test_to_prompt_block_includes_focus_and_threads_when_it_fits(tmp_path):
    model = _model(tmp_path)
    model.spine.record(Observation(source="window_focus", kind="focus.changed", ts=time.time(), subject="VS Code", dedupe_key="a"))
    model.threads.open("email_reply", "Reply to a@x.com")

    snap = model.snapshot()
    block = snap.to_prompt_block(max_chars=1200)
    assert "VS Code" in block
    assert "Reply to a@x.com" in block


def test_to_prompt_block_empty_snapshot_is_empty_string(tmp_path):
    snap = _model(tmp_path).snapshot()
    assert snap.to_prompt_block() == ""
