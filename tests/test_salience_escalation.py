import time

from argus.salience.escalation import EscalationScheduler
from argus.salience.scoring import EscalationStep
from argus.spine.store import SpineStore
from argus.world.threads import ThreadStore


def _scheduler(tmp_path, threads=None, deliver_fn=None) -> EscalationScheduler:
    return EscalationScheduler(threads=threads, deliver_fn=deliver_fn, db_path=tmp_path / "argus.db")


def _thread_store(tmp_path) -> ThreadStore:
    return ThreadStore(SpineStore(tmp_path / "spine.db"), tmp_path / "argus.db")


def test_schedule_persists_pending_steps(tmp_path):
    sched = _scheduler(tmp_path)
    now = 1_000_000.0
    ids = sched.schedule(
        text="you've got mail", thread_id=None,
        steps=[EscalationStep(after_seconds=600, channel="speak"), EscalationStep(after_seconds=1800, channel="push")],
        now=now,
    )

    pending = sched.pending()
    assert len(pending) == 2
    assert {p.id for p in pending} == set(ids)
    assert sorted(p.fire_ts for p in pending) == [now + 600, now + 1800]


def test_process_due_fires_only_steps_whose_time_has_come(tmp_path):
    delivered = []
    sched = _scheduler(tmp_path, deliver_fn=lambda channel, text: delivered.append((channel, text)))
    now = 1_000_000.0
    sched.schedule(text="soon", thread_id=None, steps=[EscalationStep(after_seconds=10, channel="speak")], now=now)
    sched.schedule(text="later", thread_id=None, steps=[EscalationStep(after_seconds=1000, channel="speak")], now=now)

    fired = sched.process_due(now=now + 20)

    assert fired == 1
    assert delivered == [("speak", "soon")]
    assert [p.text for p in sched.pending()] == ["later"]


def test_process_due_marks_fired_steps_so_they_do_not_refire(tmp_path):
    delivered = []
    sched = _scheduler(tmp_path, deliver_fn=lambda channel, text: delivered.append(text))
    now = 1_000_000.0
    sched.schedule(text="x", thread_id=None, steps=[EscalationStep(after_seconds=10, channel="speak")], now=now)

    sched.process_due(now=now + 20)
    sched.process_due(now=now + 40)

    assert delivered == ["x"]


def test_escalation_step_whose_thread_closed_in_the_interim_does_not_fire(tmp_path):
    """Phase C acceptance (§5.6)."""
    delivered = []
    threads = _thread_store(tmp_path)
    thread_id = threads.open("email_reply", "Reply to a@x.com")
    sched = _scheduler(tmp_path, threads=threads, deliver_fn=lambda channel, text: delivered.append(text))
    now = 1_000_000.0
    sched.schedule(text="still waiting on a reply", thread_id=thread_id, steps=[EscalationStep(after_seconds=10, channel="speak")], now=now)

    threads.close(thread_id, "user replied")
    fired = sched.process_due(now=now + 20)

    assert fired == 0
    assert delivered == []
    pending_row = sched.pending()
    assert pending_row == []  # aborted, not left pending forever


def test_escalation_step_whose_thread_is_still_open_fires(tmp_path):
    delivered = []
    threads = _thread_store(tmp_path)
    thread_id = threads.open("email_reply", "Reply to a@x.com")
    sched = _scheduler(tmp_path, threads=threads, deliver_fn=lambda channel, text: delivered.append(text))
    now = 1_000_000.0
    sched.schedule(text="still waiting on a reply", thread_id=thread_id, steps=[EscalationStep(after_seconds=10, channel="speak")], now=now)

    fired = sched.process_due(now=now + 20)

    assert fired == 1
    assert delivered == ["still waiting on a reply"]


def test_deliver_fn_exception_does_not_raise_and_still_marks_fired(tmp_path):
    def _boom(channel, text):
        raise RuntimeError("delivery exploded")

    sched = _scheduler(tmp_path, deliver_fn=_boom)
    now = 1_000_000.0
    sched.schedule(text="x", thread_id=None, steps=[EscalationStep(after_seconds=10, channel="speak")], now=now)

    fired = sched.process_due(now=now + 20)  # must not raise
    assert fired == 1
    assert sched.pending() == []


def test_reconciled_on_startup_fires_steps_that_came_due_while_offline(tmp_path):
    delivered = []
    now = 1_000_000.0
    db_path = tmp_path / "argus.db"

    original = EscalationScheduler(db_path=db_path)
    original.schedule(text="x", thread_id=None, steps=[EscalationStep(after_seconds=10, channel="speak")], now=now)
    # Simulates a restart: a new process, no in-memory scheduler thread
    # ever ran, but the step is long overdue by the time it comes back up.
    restarted = EscalationScheduler(db_path=db_path, deliver_fn=lambda c, t: delivered.append(t))

    fired = restarted.process_due(now=now + 3600)

    assert fired == 1
    assert delivered == ["x"]


def test_start_reconciles_immediately_then_runs_the_timer(tmp_path, monkeypatch):
    monkeypatch.setattr("argus.salience.escalation.settings.escalation_poll_seconds", 0.02)
    delivered = []
    sched = _scheduler(tmp_path, deliver_fn=lambda c, t: delivered.append(t))
    sched.schedule(text="already due", thread_id=None, steps=[EscalationStep(after_seconds=-10, channel="speak")])

    sched.start()
    time.sleep(0.05)
    sched.stop()

    assert "already due" in delivered
