from datetime import datetime, timedelta

from argus.memory.reminders import ReminderStore
from argus.memory.store import get_connection


def _store(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    return ReminderStore(conn)


def test_add_and_list_pending(tmp_path):
    store = _store(tmp_path)
    due = (datetime.now().astimezone() + timedelta(minutes=10)).isoformat()
    reminder_id = store.add("water the plants", due)

    pending = store.list_pending()
    assert len(pending) == 1
    assert pending[0]["id"] == reminder_id
    assert pending[0]["text"] == "water the plants"


def test_list_due_only_returns_due_unnotified_reminders(tmp_path):
    store = _store(tmp_path)
    now = datetime.now().astimezone()
    past = store.add("already due", (now - timedelta(minutes=5)).isoformat())
    store.add("not due yet", (now + timedelta(hours=1)).isoformat())

    due = store.list_due(now.isoformat())
    assert [r["id"] for r in due] == [past]


def test_marking_notified_excludes_from_future_due_checks(tmp_path):
    store = _store(tmp_path)
    now = datetime.now().astimezone()
    reminder_id = store.add("past reminder", (now - timedelta(minutes=1)).isoformat())

    assert len(store.list_due(now.isoformat())) == 1
    store.mark_notified(reminder_id)
    assert len(store.list_due(now.isoformat())) == 0


def test_completing_a_reminder_removes_it_from_pending(tmp_path):
    store = _store(tmp_path)
    due = (datetime.now().astimezone() + timedelta(minutes=10)).isoformat()
    reminder_id = store.add("task", due)

    store.complete(reminder_id)
    assert store.list_pending() == []
