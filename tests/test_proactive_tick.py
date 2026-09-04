"""PRD.md §19 unit 37: the proactive tick, driving every previously-
orphaned producer. Reminders get their own dedicated file
(test_proactive_tick_reminders.py) since that's the explicit
never-lost/never-spoken-twice regression the unit exists to fix."""

import threading
import time
from unittest.mock import MagicMock

from argus.orchestrator import Orchestrator
from argus.proactive_engine import ProactiveEngine
from argus.salience.scoring import Candidate, Decision, EscalationStep
from argus.spine.observation import Observation
from argus.tools.base import PermissionTier, Tool


def _isolated_engine(tmp_path, monkeypatch, speak_fn=None, interaction_lock=None) -> ProactiveEngine:
    monkeypatch.setattr("argus.config.settings.argus_data_dir", str(tmp_path))
    monkeypatch.setattr("argus.config.settings.spine_db_path", str(tmp_path / "spine.db"))
    orch = Orchestrator()
    return ProactiveEngine(orch, speak_fn=speak_fn or MagicMock(), interaction_lock=interaction_lock or threading.Lock())


# -- rule firing (step 3) -------------------------------------------------

def test_matching_automation_rule_fires_in_production_not_just_in_a_test(tmp_path, monkeypatch):
    """§19's own framing: a unit's tests call fire() directly, so
    "nothing calls it in prod" passes every test. This one goes through
    the real tick instead -- record a real observation, let the real
    tick match it against a real rule and call fire() itself."""
    engine = _isolated_engine(tmp_path, monkeypatch)
    calls = []
    engine.orchestrator.tools.register(Tool(
        name="do_thing", description="x", input_schema={"type": "object", "properties": {}},
        tier=PermissionTier.ALLOW, handler=lambda a: (calls.append(a), "ok")[1],
    ))
    rule_id = engine.orchestrator.rule_store.propose(
        natural_language="run do_thing when a task fails", kind="automation",
        trigger={"kind": "task.failed", "filters": []},
        action={"type": "tool_call", "tool": "do_thing", "arguments": {"x": 1}},
    )
    engine.orchestrator.rule_store.confirm(rule_id)

    engine.spine.record(Observation(source="test", kind="task.failed", ts=time.time(), payload={}))

    engine._run_proactive_tick(now=time.time())

    assert calls == [{"x": 1}]


def test_non_matching_rule_does_not_fire(tmp_path, monkeypatch):
    engine = _isolated_engine(tmp_path, monkeypatch)
    calls = []
    engine.orchestrator.tools.register(Tool(
        name="do_thing", description="x", input_schema={"type": "object", "properties": {}},
        tier=PermissionTier.ALLOW, handler=lambda a: (calls.append(a), "ok")[1],
    ))
    rule_id = engine.orchestrator.rule_store.propose(
        natural_language="run do_thing when a task fails", kind="automation",
        trigger={"kind": "task.failed", "filters": []},
        action={"type": "tool_call", "tool": "do_thing", "arguments": {}},
    )
    engine.orchestrator.rule_store.confirm(rule_id)

    engine.spine.record(Observation(source="test", kind="git.commit", ts=time.time(), payload={}))

    engine._run_proactive_tick(now=time.time())

    assert calls == []


def test_a_failing_rule_fire_does_not_block_other_rules_that_tick(tmp_path, monkeypatch):
    engine = _isolated_engine(tmp_path, monkeypatch)
    calls = []
    engine.orchestrator.tools.register(Tool(
        name="broken_tool", description="x", input_schema={"type": "object", "properties": {}},
        tier=PermissionTier.ALLOW, handler=lambda a: 1 / 0,
    ))
    engine.orchestrator.tools.register(Tool(
        name="good_tool", description="x", input_schema={"type": "object", "properties": {}},
        tier=PermissionTier.ALLOW, handler=lambda a: (calls.append(a), "ok")[1],
    ))
    for tool_name in ("broken_tool", "good_tool"):
        rule_id = engine.orchestrator.rule_store.propose(
            natural_language=f"run {tool_name}", kind="automation",
            trigger={"kind": "task.failed", "filters": []},
            action={"type": "tool_call", "tool": tool_name, "arguments": {}},
        )
        engine.orchestrator.rule_store.confirm(rule_id)

    engine.spine.record(Observation(source="test", kind="task.failed", ts=time.time(), payload={}))

    engine._run_proactive_tick(now=time.time())  # must not raise

    assert calls == [{}]


# -- system_health thread wiring (PRD §19/§20 unit 44c) --------------------

def test_credential_failed_observation_opens_a_system_health_thread(tmp_path, monkeypatch):
    """44c: 'the credential_failed observation opens a system_health
    thread that the §19 tick can reap once the credential works again --
    that's the point of it, not just logging.' Nothing in the codebase
    called open_system_health() in response to a health observation
    before this unit -- this is the wiring that makes it happen."""
    engine = _isolated_engine(tmp_path, monkeypatch)

    engine.spine.record(Observation(
        source="argus.health", kind="argus.credential_failed", ts=time.time(),
        subject="Yahoo", payload={"error": "AUTHENTICATIONFAILED"},
    ))

    engine._run_proactive_tick(now=time.time())

    thread = engine.threads.find_open("system_health", "Yahoo")
    assert thread is not None
    assert "Yahoo" in thread.title


def test_repeated_credential_failed_observations_touch_one_thread_not_many(tmp_path, monkeypatch):
    engine = _isolated_engine(tmp_path, monkeypatch)
    now = time.time()

    engine.spine.record(Observation(source="argus.health", kind="argus.credential_failed", ts=now, subject="Yahoo", payload={}))
    engine._run_proactive_tick(now=now)
    first = engine.threads.find_open("system_health", "Yahoo")

    engine.spine.record(Observation(source="argus.health", kind="argus.credential_failed", ts=now + 1, subject="Yahoo", payload={}))
    engine._run_proactive_tick(now=now + 1)
    second = engine.threads.find_open("system_health", "Yahoo")

    assert first.id == second.id


def test_credential_recovered_observation_closes_the_system_health_thread(tmp_path, monkeypatch):
    """The other half of the acceptance box: reap-eligible once the
    credential works again."""
    engine = _isolated_engine(tmp_path, monkeypatch)

    engine.spine.record(Observation(source="argus.health", kind="argus.credential_failed", ts=time.time(), subject="Yahoo", payload={}))
    engine._run_proactive_tick(now=time.time())
    thread_id = engine.threads.find_open("system_health", "Yahoo").id

    # A tick boundary that lands exactly on an observation's own ts can, by
    # this tick's own documented design (_tick_rule_firing's docstring),
    # see that observation again next tick -- advance well clear of it so
    # this test isn't exercising that narrow, accepted race.
    engine.spine.record(Observation(source="argus.health", kind="argus.credential_recovered", ts=time.time() + 5, subject="Yahoo", payload={}))
    engine._run_proactive_tick(now=time.time() + 5)

    assert engine.threads.get(thread_id).closed_ts is not None
    assert engine.threads.find_open("system_health", "Yahoo") is None


def test_credential_recovered_with_no_open_thread_is_a_safe_noop(tmp_path, monkeypatch):
    engine = _isolated_engine(tmp_path, monkeypatch)

    engine.spine.record(Observation(source="argus.health", kind="argus.credential_recovered", ts=time.time(), subject="Yahoo", payload={}))

    engine._run_proactive_tick(now=time.time())  # must not raise


# -- reap on a timer (step 4) ----------------------------------------------

def test_thread_with_a_timeout_close_condition_closes_on_the_timer_with_no_acknowledgment(tmp_path, monkeypatch):
    engine = _isolated_engine(tmp_path, monkeypatch)
    thread_id = engine.threads.open("commitment", "call the dentist", close_condition={"type": "timeout", "seconds": 1})

    assert engine.threads.get(thread_id).closed_ts is None

    engine._run_proactive_tick(now=time.time() + 10)  # well past the 1s timeout

    assert engine.threads.get(thread_id).closed_ts is not None


def test_reap_is_gated_by_thread_reap_seconds_not_every_tick(tmp_path, monkeypatch):
    engine = _isolated_engine(tmp_path, monkeypatch)
    engine.threads.reap = MagicMock(return_value=0)
    now = time.time()

    engine._run_proactive_tick(now=now)  # first tick always reaps (_last_reap_ts starts at 0)
    assert engine.threads.reap.call_count == 1

    engine._run_proactive_tick(now=now + 1)  # 1s later -- well under thread_reap_seconds
    assert engine.threads.reap.call_count == 1

    from argus.config import settings
    engine._run_proactive_tick(now=now + settings.thread_reap_seconds + 1)
    assert engine.threads.reap.call_count == 2


# -- escalation (step 5) ---------------------------------------------------

def test_a_spoken_candidate_with_escalation_steps_calls_schedule(tmp_path, monkeypatch):
    """The producer side: today nothing calls schedule() at all, so the
    scheduler's own drain timer only ever drains an empty queue."""
    engine = _isolated_engine(tmp_path, monkeypatch)
    steps = [EscalationStep(after_seconds=60, channel="speak")]
    monkeypatch.setattr(
        engine.salience_engine, "decide",
        lambda *a, **k: Decision(action="speak", reason="test", escalation=steps),
    )
    candidate = Candidate(observation_id=None, kind="task.failed", subject=None, text="hi", base_urgency=0.9)

    engine.dispatcher.submit(candidate, now=time.time())

    pending = engine.escalation_scheduler.pending()
    assert len(pending) == 1
    assert pending[0].channel == "speak"


def test_a_held_candidate_with_escalation_steps_does_not_call_schedule(tmp_path, monkeypatch):
    """Escalating a follow-up to something the user never actually heard
    would be worse than not escalating at all."""
    engine = _isolated_engine(tmp_path, monkeypatch, interaction_lock=threading.Lock())
    engine.dispatcher._interaction_lock.acquire()  # simulate busy
    steps = [EscalationStep(after_seconds=60, channel="speak")]
    monkeypatch.setattr(
        engine.salience_engine, "decide",
        lambda *a, **k: Decision(action="speak", reason="test", escalation=steps),
    )
    candidate = Candidate(observation_id=None, kind="task.failed", subject=None, text="hi", base_urgency=0.9)

    engine.dispatcher.submit(candidate, now=time.time())

    assert engine.escalation_scheduler.pending() == []


def test_tick_drains_due_escalation_steps(tmp_path, monkeypatch):
    engine = _isolated_engine(tmp_path, monkeypatch)
    delivered = []
    engine.escalation_scheduler.deliver_fn = lambda channel, text: delivered.append((channel, text))
    engine.escalation_scheduler.schedule(text="follow up", steps=[EscalationStep(after_seconds=5, channel="speak")], now=time.time())

    engine._run_proactive_tick(now=time.time() + 10)  # past the 5s escalation delay

    assert delivered == [("speak", "follow up")]


# -- induction (step 6) -----------------------------------------------------

def test_induction_runs_at_most_daily_and_only_proposes(tmp_path, monkeypatch):
    engine = _isolated_engine(tmp_path, monkeypatch)
    engine.induction_engine.run_once = MagicMock(return_value=[])
    now = time.time()

    engine._run_proactive_tick(now=now)
    assert engine.induction_engine.run_once.call_count == 1

    engine._run_proactive_tick(now=now + 10)  # far short of a day later
    assert engine.induction_engine.run_once.call_count == 1

    engine._run_proactive_tick(now=now + 90_000)  # more than a day later
    assert engine.induction_engine.run_once.call_count == 2


def test_induction_only_proposes_never_activates(tmp_path, monkeypatch):
    """InductionEngine.run_once() already guarantees this on its own
    (origin='induced', status='proposed') -- this just proves the tick
    actually reaches it in production and the guarantee still holds."""
    engine = _isolated_engine(tmp_path, monkeypatch)
    now = time.time()
    for i in range(4):
        engine.held.add(kind="git.branch_stale", subject=None, text="x", score=0.2)
    for item in engine.held.pending():
        engine.held.dismiss(item.id)

    engine._run_proactive_tick(now=now)

    proposed = engine.orchestrator.rule_store.list_by_origin("induced")
    assert len(proposed) == 1
    assert proposed[0].status == "proposed"


# -- retention (step 7) -----------------------------------------------------

def test_prune_runs_and_old_observations_are_deleted(tmp_path, monkeypatch):
    from argus.config import settings

    engine = _isolated_engine(tmp_path, monkeypatch)
    old_ts = time.time() - (settings.spine_retention_days + 10) * 86400
    engine.spine.record(Observation(source="test", kind="git.commit", ts=old_ts, payload={}))
    assert engine.spine.count() == 1

    engine._run_proactive_tick(now=time.time())

    assert engine.spine.count() == 0


def test_retention_is_gated_to_at_most_once_a_day(tmp_path, monkeypatch):
    engine = _isolated_engine(tmp_path, monkeypatch)
    engine.spine.prune = MagicMock(return_value=0)
    now = time.time()

    engine._run_proactive_tick(now=now)
    assert engine.spine.prune.call_count == 1

    engine._run_proactive_tick(now=now + 10)
    assert engine.spine.prune.call_count == 1

    engine._run_proactive_tick(now=now + 90_000)
    assert engine.spine.prune.call_count == 2


# -- per-step isolation -----------------------------------------------------

def test_one_failing_step_does_not_stop_the_others_that_tick(tmp_path, monkeypatch):
    engine = _isolated_engine(tmp_path, monkeypatch)
    engine._tick_reminders = MagicMock(side_effect=RuntimeError("boom"))
    engine._tick_rule_firing = MagicMock()
    engine._tick_reap = MagicMock()
    engine._tick_escalation = MagicMock()
    engine._tick_induction = MagicMock()
    engine._tick_retention = MagicMock()

    engine._run_proactive_tick(now=time.time())  # must not raise

    engine._tick_rule_firing.assert_called_once()
    engine._tick_reap.assert_called_once()
    engine._tick_escalation.assert_called_once()
    engine._tick_induction.assert_called_once()
    engine._tick_retention.assert_called_once()


def test_every_step_fails_independently_not_just_the_first(tmp_path, monkeypatch):
    engine = _isolated_engine(tmp_path, monkeypatch)
    for name in ("_tick_reminders", "_tick_rule_firing", "_tick_reap", "_tick_escalation", "_tick_induction", "_tick_retention"):
        setattr(engine, name, MagicMock(side_effect=RuntimeError("boom")))

    engine._run_proactive_tick(now=time.time())  # must not raise, all six fail
