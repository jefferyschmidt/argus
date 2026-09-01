import time
from unittest.mock import MagicMock

from argus.proactive_engine import ProactiveEngine

# Data-dir isolation for the stores ProactiveEngine now constructs
# (spine/threads/rules/budget/held/rhythms) comes from conftest.py's
# autouse _isolated_data_dir fixture -- no per-file fixture needed here.


def _orchestrator():
    orch = MagicMock()
    orch.router = MagicMock()
    orch.memory = MagicMock()
    orch.task_runner = None  # matches the real default (enable_task_runner=False)
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


def test_constructs_the_salience_stack():
    """U-C4 (PRD §5/§7): the perception/salience stack this engine now
    also owns and wires every retrofitted worker through."""
    engine = ProactiveEngine(_orchestrator(), speak_fn=MagicMock(), interaction_lock=MagicMock())

    assert engine.spine is not None
    assert engine.spine_engine is not None
    assert engine.threads is not None
    assert engine.rhythms is not None
    assert engine.world_model is not None
    assert engine.rule_matcher is not None
    assert engine.budget is not None
    assert engine.held is not None
    assert engine.salience_engine is not None
    assert engine.dispatcher is not None
    assert engine.escalation_scheduler is not None


def test_start_runs_every_worker_on_its_own_thread():
    engine = ProactiveEngine(_orchestrator(), speak_fn=MagicMock(), interaction_lock=MagicMock())
    workers = (
        engine.context_awareness, engine.email_watcher, engine.routine_worker,
        engine.knowledge_watcher, engine.research_digest, engine.stuck_detection,
        engine.consolidation_worker,
    )
    for worker in workers:
        worker.run = MagicMock()
    engine.spine_engine.start = MagicMock()
    engine.escalation_scheduler.start = MagicMock()

    engine.start()
    time.sleep(0.2)  # let the daemon threads actually get scheduled

    for worker in workers:
        worker.run.assert_called_once()
    engine.spine_engine.start.assert_called_once()
    engine.escalation_scheduler.start.assert_called_once()


def test_all_retrofitted_workers_share_one_dispatcher():
    """U-C4: no worker holds speak_fn/interaction_lock directly anymore
    (except routine_worker -- see its own docstring for why); they all
    submit through the one SalienceDispatcher this engine constructs."""
    engine = ProactiveEngine(_orchestrator(), speak_fn=MagicMock(), interaction_lock=MagicMock())

    assert engine.context_awareness._dispatcher is engine.dispatcher
    assert engine.email_watcher._dispatcher is engine.dispatcher
    assert engine.knowledge_watcher._dispatcher is engine.dispatcher
    assert engine.research_digest._dispatcher is engine.dispatcher
    assert engine.stuck_detection._dispatcher is engine.dispatcher


def test_dispatcher_receives_the_shared_speak_fn_and_lock():
    speak_fn = MagicMock()
    lock = MagicMock()

    engine = ProactiveEngine(_orchestrator(), speak_fn=speak_fn, interaction_lock=lock)

    assert engine.dispatcher._speak_fn is speak_fn
    assert engine.dispatcher._interaction_lock is lock


def test_routine_worker_still_receives_speak_fn_and_lock_directly():
    """The one documented exception -- see routine_worker.py."""
    speak_fn = MagicMock()
    lock = MagicMock()

    engine = ProactiveEngine(_orchestrator(), speak_fn=speak_fn, interaction_lock=lock)

    assert engine.routine_worker._speak_fn is speak_fn
    assert engine.routine_worker._interaction_lock is lock


def test_email_watcher_can_open_threads():
    engine = ProactiveEngine(_orchestrator(), speak_fn=MagicMock(), interaction_lock=MagicMock())
    assert engine.email_watcher._threads is engine.threads


# -- task runner reconciliation (Phase I, PRD §6) ----------------------

def test_start_does_not_touch_task_runner_when_absent():
    orch = _orchestrator()
    engine = ProactiveEngine(orch, speak_fn=MagicMock(), interaction_lock=MagicMock())
    for worker in (
        engine.context_awareness, engine.email_watcher, engine.routine_worker,
        engine.knowledge_watcher, engine.research_digest, engine.stuck_detection,
        engine.consolidation_worker,
    ):
        worker.run = MagicMock()
    engine.spine_engine.start = MagicMock()
    engine.escalation_scheduler.start = MagicMock()

    engine.start()  # must not raise just because orch.task_runner is None
    time.sleep(0.1)


def test_start_reconciles_the_task_runner_when_present():
    orch = _orchestrator()
    orch.task_runner = MagicMock()
    engine = ProactiveEngine(orch, speak_fn=MagicMock(), interaction_lock=MagicMock())
    for worker in (
        engine.context_awareness, engine.email_watcher, engine.routine_worker,
        engine.knowledge_watcher, engine.research_digest, engine.stuck_detection,
        engine.consolidation_worker,
    ):
        worker.run = MagicMock()
    engine.spine_engine.start = MagicMock()
    engine.escalation_scheduler.start = MagicMock()

    engine.start()
    time.sleep(0.1)

    orch.task_runner.reconcile_on_startup.assert_called_once()


def test_task_runner_reconciliation_failure_does_not_stop_other_subsystems():
    """PRD §5.2's isolation rule: 'a failure there must degrade
    proactivity, never prevent Argus from starting.' A crashing
    reconcile_on_startup() must not take spine sensors, the escalation
    scheduler, or any worker down with it."""
    orch = _orchestrator()
    orch.task_runner = MagicMock()
    orch.task_runner.reconcile_on_startup.side_effect = RuntimeError("db exploded")
    engine = ProactiveEngine(orch, speak_fn=MagicMock(), interaction_lock=MagicMock())
    workers = (
        engine.context_awareness, engine.email_watcher, engine.routine_worker,
        engine.knowledge_watcher, engine.research_digest, engine.stuck_detection,
        engine.consolidation_worker,
    )
    for worker in workers:
        worker.run = MagicMock()
    engine.spine_engine.start = MagicMock()
    engine.escalation_scheduler.start = MagicMock()

    engine.start()  # must not raise
    time.sleep(0.2)

    engine.spine_engine.start.assert_called_once()
    engine.escalation_scheduler.start.assert_called_once()
    for worker in workers:
        worker.run.assert_called_once()
