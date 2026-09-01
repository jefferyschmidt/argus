from datetime import datetime, timedelta, timezone

from argus.memory.store import get_connection
from argus.spine.sensors.reminder import ReminderSensor


def test_due_reminder_emits_reminder_due_and_is_marked_notified(tmp_path, monkeypatch):
    db_path = tmp_path / "argus.db"
    conn = get_connection(db_path)
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    conn.execute("INSERT INTO reminders (due_at, text) VALUES (?, ?)", (past, "call the dentist"))
    conn.commit()
    conn.close()

    monkeypatch.setattr("argus.memory.store.get_connection", lambda path=None: get_connection(db_path))

    sensor = ReminderSensor()
    observations = sensor.poll()

    assert len(observations) == 1
    assert observations[0].kind == "reminder.due"
    assert observations[0].payload["text"] == "call the dentist"

    # marked notified -- a second poll must not re-emit it
    second = sensor.poll()
    assert second == []


def test_no_due_reminders_returns_empty(tmp_path, monkeypatch):
    db_path = tmp_path / "argus.db"
    monkeypatch.setattr("argus.memory.store.get_connection", lambda path=None: get_connection(db_path))

    assert ReminderSensor().poll() == []
