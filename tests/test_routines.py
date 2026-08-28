from datetime import datetime

from argus.memory.routines import RoutineStore
from argus.memory.store import get_connection
from argus.tools.routines import _cancel_scheduled_routine, _create_scheduled_routine, _list_scheduled_routines


def _store(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    return RoutineStore(conn)


def test_add_and_list_all(tmp_path):
    store = _store(tmp_path)
    store.add("07:00", "give me a morning briefing")
    rows = store.list_all()
    assert len(rows) == 1
    assert rows[0]["time_of_day"] == "07:00"
    assert rows[0]["enabled"] == 1


def test_due_when_time_has_passed_and_not_run_today(tmp_path):
    store = _store(tmp_path)
    store.add("07:00", "briefing")
    now = datetime(2026, 1, 15, 8, 0)  # 8am, past the 7am trigger
    due = store.list_due(now)
    assert len(due) == 1


def test_not_due_before_scheduled_time(tmp_path):
    store = _store(tmp_path)
    store.add("07:00", "briefing")
    now = datetime(2026, 1, 15, 6, 30)  # 6:30am, before 7am
    assert store.list_due(now) == []


def test_not_due_again_same_day_after_running(tmp_path):
    store = _store(tmp_path)
    routine_id = store.add("07:00", "briefing")
    now = datetime(2026, 1, 15, 8, 0)
    assert len(store.list_due(now)) == 1

    store.mark_run(routine_id, now)
    assert store.list_due(now) == []

    # But due again the next day.
    tomorrow = datetime(2026, 1, 16, 8, 0)
    assert len(store.list_due(tomorrow)) == 1


def test_disabled_routine_is_never_due(tmp_path):
    store = _store(tmp_path)
    routine_id = store.add("07:00", "briefing")
    store.cancel(routine_id)
    now = datetime(2026, 1, 15, 8, 0)
    assert store.list_due(now) == []


def test_create_tool_rejects_bad_time_format():
    result = _create_scheduled_routine({"time_of_day": "7am", "goal": "briefing"})
    assert result.startswith("error:")


def test_create_list_cancel_tool_round_trip(monkeypatch, tmp_path):
    monkeypatch.setattr("argus.tools.routines.get_connection", lambda: get_connection(tmp_path / "test.db"))

    created = _create_scheduled_routine({"time_of_day": "07:00", "goal": "morning briefing"})
    assert "07:00" in created

    listed = _list_scheduled_routines({})
    assert "morning briefing" in listed
    assert "enabled" in listed

    routine_id = int(listed.split("#")[1].split(":")[0])
    cancelled = _cancel_scheduled_routine({"routine_id": routine_id})
    assert str(routine_id) in cancelled

    listed_after = _list_scheduled_routines({})
    assert "disabled" in listed_after
