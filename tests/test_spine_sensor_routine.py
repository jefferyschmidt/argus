from datetime import datetime, timedelta

from argus.memory.store import get_connection
from argus.spine.sensors.routine import RoutineSensor


def test_due_routine_emits_routine_due(tmp_path, monkeypatch):
    db_path = tmp_path / "argus.db"
    conn = get_connection(db_path)
    past_hm = (datetime.now() - timedelta(minutes=5)).strftime("%H:%M")
    conn.execute("INSERT INTO routines (time_of_day, goal) VALUES (?, ?)", (past_hm, "morning briefing"))
    conn.commit()
    conn.close()

    monkeypatch.setattr("argus.memory.store.get_connection", lambda path=None: get_connection(db_path))

    observations = RoutineSensor().poll()

    assert len(observations) == 1
    assert observations[0].kind == "routine.due"
    assert observations[0].payload["goal"] == "morning briefing"


def test_routine_already_run_today_is_not_reported(tmp_path, monkeypatch):
    db_path = tmp_path / "argus.db"
    conn = get_connection(db_path)
    past_hm = (datetime.now() - timedelta(minutes=5)).strftime("%H:%M")
    today = datetime.now().date().isoformat()
    conn.execute(
        "INSERT INTO routines (time_of_day, goal, last_run_date) VALUES (?, ?, ?)",
        (past_hm, "morning briefing", today),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr("argus.memory.store.get_connection", lambda path=None: get_connection(db_path))

    assert RoutineSensor().poll() == []


def test_no_routines_returns_empty(tmp_path, monkeypatch):
    db_path = tmp_path / "argus.db"
    monkeypatch.setattr("argus.memory.store.get_connection", lambda path=None: get_connection(db_path))

    assert RoutineSensor().poll() == []
