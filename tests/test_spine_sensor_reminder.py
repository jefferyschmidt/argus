from datetime import datetime, timedelta, timezone

from argus.memory.store import get_connection
from argus.spine.sensors.reminder import ReminderSensor


def test_due_reminder_emits_reminder_due(tmp_path, monkeypatch):
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


def test_poll_does_not_mutate_reminder_state(tmp_path, monkeypatch):
    """PRD §19 unit 38: a sensor observes, it does not decide or mutate.
    poll() must contain no write to reminder state -- delivery (and the
    mark_notified call) belongs solely to ProactiveEngine's reminder
    tick step (unit 37), after confirmed delivery. Proven here at the
    behavioral level: an undelivered reminder is still due, and
    unchanged (not completed), after poll()."""
    from argus.memory.reminders import ReminderStore

    db_path = tmp_path / "argus.db"
    conn = get_connection(db_path)
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    conn.execute("INSERT INTO reminders (due_at, text) VALUES (?, ?)", (past, "call the dentist"))
    conn.commit()
    conn.close()

    monkeypatch.setattr("argus.memory.store.get_connection", lambda path=None: get_connection(db_path))

    ReminderSensor().poll()
    ReminderSensor().poll()  # a second poll, nothing delivered it in between

    conn = get_connection(db_path)
    try:
        store = ReminderStore(conn)
        # Still due (poll() never marked it notified) -- the delivery
        # decision was never this sensor's to make.
        still_due = store.list_due(datetime.now(timezone.utc).isoformat())
        assert len(still_due) == 1
        assert still_due[0]["text"] == "call the dentist"
    finally:
        conn.close()


def test_no_due_reminders_returns_empty(tmp_path, monkeypatch):
    db_path = tmp_path / "argus.db"
    monkeypatch.setattr("argus.memory.store.get_connection", lambda path=None: get_connection(db_path))

    assert ReminderSensor().poll() == []
