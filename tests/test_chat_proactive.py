"""PRD.md §19 unit 40 (Part 1, item 2): argus chat previously constructed
an Orchestrator but never a ProactiveEngine -- reminders/threads/rules
never ran there at all. cli.py::_start_chat_proactive is the wiring
that fixes it, split out of chat() itself (which otherwise only runs as
a blocking console.input() REPL) so it's directly testable."""

import threading
import time
from datetime import datetime, timedelta

from argus.cli import _start_chat_proactive
from argus.memory.reminders import ReminderStore
from argus.memory.store import get_connection
from argus.orchestrator import Orchestrator
from argus.proactive_engine import ProactiveEngine
from argus.salience.scoring import Decision


def _isolated_orch(tmp_path, monkeypatch) -> Orchestrator:
    monkeypatch.setattr("argus.config.settings.argus_data_dir", str(tmp_path))
    monkeypatch.setattr("argus.config.settings.spine_db_path", str(tmp_path / "spine.db"))
    return Orchestrator()


def test_chat_constructs_and_starts_a_real_proactive_engine(tmp_path, monkeypatch):
    started = []
    monkeypatch.setattr("argus.proactive_engine.ProactiveEngine.start", lambda self: started.append(True))
    orch = _isolated_orch(tmp_path, monkeypatch)

    engine = _start_chat_proactive(orch, threading.Lock())

    assert isinstance(engine, ProactiveEngine)
    assert engine.orchestrator is orch
    assert started == [True]  # not just constructed -- actually started


def test_a_due_reminder_surfaces_as_text_in_chat(tmp_path, monkeypatch, capsys):
    """The acceptance box, literally: a due reminder is spoken -- for
    chat, "spoken" means printed -- through the SAME tick -> dispatcher
    -> speak_fn chain voice mode uses, not a chat-specific shortcut.

    Stubs SalienceEngine.decide() to a fixed "speak" Decision, matching
    test_proactive_tick_reminders.py's and test_realtime_e2e.py's own
    established convention -- salience's threshold/scoring math is a
    separate, already-covered subsystem."""
    monkeypatch.setattr("argus.proactive_engine.ProactiveEngine.start", lambda self: None)
    orch = _isolated_orch(tmp_path, monkeypatch)
    engine = _start_chat_proactive(orch, threading.Lock())
    monkeypatch.setattr(engine.salience_engine, "decide", lambda *a, **k: Decision(action="speak", reason="test"))

    conn = get_connection()
    try:
        store = ReminderStore(conn)
        now = time.time()
        due_iso = (datetime.now().astimezone() - timedelta(minutes=1)).isoformat()
        store.add("water the plants", due_iso)

        engine._tick_reminders(now=now)

        out = capsys.readouterr().out
        assert "water the plants" in out
        assert "argus>" in out  # printed the same way an ordinary reply is

        due_after = store.list_due(datetime.now().astimezone().isoformat())
        assert not any(r["text"] == "water the plants" for r in due_after), (
            "reminder must be marked notified only after delivery"
        )
    finally:
        conn.close()


def test_proactive_construction_failure_does_not_crash_chat(tmp_path, monkeypatch):
    """PRD §19 unit 40 (Part 1): the safety net this item is actually
    for -- this is the first entry point where a conversation is
    expected to keep working with NO proactive layer at all if
    construction itself fails."""
    def boom(*args, **kwargs):
        raise RuntimeError("boom")
    monkeypatch.setattr("argus.proactive_engine.start_proactive_engine", boom)
    orch = _isolated_orch(tmp_path, monkeypatch)

    result = _start_chat_proactive(orch, threading.Lock())  # must not raise

    assert result is None
