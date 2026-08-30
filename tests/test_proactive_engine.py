import time
from unittest.mock import MagicMock, patch

from argus.proactive_engine import ProactiveEngine


def _orchestrator():
    orch = MagicMock()
    orch.router = MagicMock()
    orch.memory = MagicMock()
    return orch


def test_constructs_all_seven_workers():
    """ROADMAP.md Phase 2: these are the exact 7 workers that were
    confirmed orphaned in RealtimeVoiceLoop (everything except reminder
    checking, which stays inline in VoiceLoop for now)."""
    engine = ProactiveEngine(_orchestrator(), speak_fn=MagicMock(), interaction_lock=MagicMock())

    assert engine.context_awareness is not None
    assert engine.email_watcher is not None
    assert engine.routine_worker is not None
    assert engine.knowledge_watcher is not None
    assert engine.research_digest is not None
    assert engine.stuck_detection is not None
    assert engine.consolidation_worker is not None


def test_start_runs_every_worker_on_its_own_thread():
    engine = ProactiveEngine(_orchestrator(), speak_fn=MagicMock(), interaction_lock=MagicMock())
    workers = (
        engine.context_awareness, engine.email_watcher, engine.routine_worker,
        engine.knowledge_watcher, engine.research_digest, engine.stuck_detection,
        engine.consolidation_worker,
    )
    for worker in workers:
        worker.run = MagicMock()

    engine.start()
    time.sleep(0.2)  # let the daemon threads actually get scheduled

    for worker in workers:
        worker.run.assert_called_once()


def test_workers_receive_the_shared_speak_fn_and_lock():
    """Construction only relocates -- each worker must still be handed the
    exact speak_fn/interaction_lock the caller passed in, not something
    ProactiveEngine invented, so its existing retry-when-busy behavior
    (e.g. email_watcher's _pending_delivery) is unaffected."""
    speak_fn = MagicMock()
    lock = MagicMock()

    with patch("argus.email_watcher.EmailWatcher.__init__", return_value=None) as email_init:
        ProactiveEngine(_orchestrator(), speak_fn=speak_fn, interaction_lock=lock)

    args, _ = email_init.call_args
    assert speak_fn in args
    assert lock in args
