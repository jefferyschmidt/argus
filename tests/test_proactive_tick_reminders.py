"""PRD.md §19 units 37+38: the user-visible regression this reliability
pass exists to fix. reminder.due was emitted and the reminder marked
notified in the sensor, but nothing ever turned it into speech --
reminders were silently lost. This is the explicit test the unit
instructions asked for: a reminder must never be lost and never spoken
twice.

Mocks SalienceEngine.decide() to a fixed Decision, not the mechanism
under test -- everything downstream of that (SalienceDispatcher's real
_deliver()/interaction-lock handling, ProactiveEngine's real tick,
ReminderStore's real notified flag) runs for real. Salience's own
threshold/scoring behavior is already covered elsewhere
(test_salience_engine.py, test_salience_scoring.py); this file is about
what happens once a decision comes back, not how that decision is
reached."""

import threading
import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from argus.memory.reminders import ReminderStore
from argus.memory.store import get_connection
from argus.orchestrator import Orchestrator
from argus.proactive_engine import ProactiveEngine
from argus.salience.scoring import Decision


def _isolated_engine(tmp_path, monkeypatch, speak_fn=None, interaction_lock=None) -> ProactiveEngine:
    monkeypatch.setattr("argus.config.settings.argus_data_dir", str(tmp_path))
    monkeypatch.setattr("argus.config.settings.spine_db_path", str(tmp_path / "spine.db"))
    orch = Orchestrator()
    return ProactiveEngine(orch, speak_fn=speak_fn or MagicMock(), interaction_lock=interaction_lock or threading.Lock())


def _add_due_reminder(text: str) -> int:
    conn = get_connection()
    try:
        past = (datetime.now().astimezone() - timedelta(minutes=1)).isoformat()
        return ReminderStore(conn).add(text, past)
    finally:
        conn.close()


def _still_due(text: str) -> bool:
    conn = get_connection()
    try:
        due = ReminderStore(conn).list_due(datetime.now().astimezone().isoformat())
        return any(row["text"] == text for row in due)
    finally:
        conn.close()


def test_delivered_reminder_is_marked_notified_and_never_spoken_again(tmp_path, monkeypatch):
    spoken = []
    engine = _isolated_engine(tmp_path, monkeypatch, speak_fn=spoken.append)
    monkeypatch.setattr(engine.salience_engine, "decide", lambda *a, **k: Decision(action="speak", reason="test"))
    _add_due_reminder("call mom")

    now = time.time()
    engine._run_proactive_tick(now=now)

    assert spoken == ["Reminder: call mom"]
    assert not _still_due("call mom")

    # A second tick must not re-speak it -- it's no longer due.
    engine._run_proactive_tick(now=now + 1)
    assert spoken == ["Reminder: call mom"]


def test_reminder_blocked_by_a_busy_lock_is_never_lost_and_retries_next_tick(tmp_path, monkeypatch):
    """The core of the fix: mark notified ONLY after confirmed delivery.
    A reminder due while Argus is mid-conversation must stay due (not
    silently marked handled) and must be delivered -- exactly once --
    once Argus is free again."""
    spoken = []
    lock = threading.Lock()
    engine = _isolated_engine(tmp_path, monkeypatch, speak_fn=spoken.append, interaction_lock=lock)
    monkeypatch.setattr(engine.salience_engine, "decide", lambda *a, **k: Decision(action="speak", reason="test"))
    _add_due_reminder("call mom")

    lock.acquire()  # simulate "Argus mid-conversation"
    now = time.time()
    engine._run_proactive_tick(now=now)

    assert spoken == []  # not delivered -- Argus was busy
    assert _still_due("call mom")  # never lost: still due, not marked notified
    assert len(engine.held.pending()) == 1  # nothing silently dropped either (§5.4)

    lock.release()
    engine._run_proactive_tick(now=now + 1)

    assert spoken == ["Reminder: call mom"]  # delivered on retry
    assert not _still_due("call mom")

    # A third tick must not speak it again -- delivered exactly once
    # across the whole retry sequence.
    engine._run_proactive_tick(now=now + 2)
    assert spoken == ["Reminder: call mom"]


def test_a_held_reminder_is_retried_every_tick_until_delivered(tmp_path, monkeypatch):
    """Not just "retries once" -- keeps retrying for as long as it takes,
    same as the email watcher's old _pending_delivery loop did, except
    durably (ReminderStore.list_due() itself is the retry queue, so this
    survives a restart the in-memory version never would)."""
    spoken = []
    lock = threading.Lock()
    engine = _isolated_engine(tmp_path, monkeypatch, speak_fn=spoken.append, interaction_lock=lock)
    monkeypatch.setattr(engine.salience_engine, "decide", lambda *a, **k: Decision(action="speak", reason="test"))
    _add_due_reminder("call mom")

    lock.acquire()
    now = time.time()
    for i in range(3):
        engine._run_proactive_tick(now=now + i)
        assert spoken == []
        assert _still_due("call mom")

    lock.release()
    engine._run_proactive_tick(now=now + 3)
    assert spoken == ["Reminder: call mom"]
    assert not _still_due("call mom")


def test_multiple_due_reminders_are_each_delivered_and_notified_independently(tmp_path, monkeypatch):
    spoken = []
    engine = _isolated_engine(tmp_path, monkeypatch, speak_fn=spoken.append)
    monkeypatch.setattr(engine.salience_engine, "decide", lambda *a, **k: Decision(action="speak", reason="test"))
    _add_due_reminder("call mom")
    _add_due_reminder("pick up dry cleaning")

    engine._run_proactive_tick(now=time.time())

    assert sorted(spoken) == ["Reminder: call mom", "Reminder: pick up dry cleaning"]
    assert not _still_due("call mom")
    assert not _still_due("pick up dry cleaning")


def test_no_due_reminders_speaks_nothing(tmp_path, monkeypatch):
    spoken = []
    engine = _isolated_engine(tmp_path, monkeypatch, speak_fn=spoken.append)

    engine._run_proactive_tick(now=time.time())

    assert spoken == []
