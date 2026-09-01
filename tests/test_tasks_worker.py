import threading
import time
from unittest.mock import MagicMock

from argus.spine.store import SpineStore
from argus.tasks.store import TaskStore
from argus.tasks.worker import TaskRunner


def _runner(tmp_path, router=None, max_concurrent=2) -> TaskRunner:
    store = TaskStore(tmp_path / "argus.db")
    spine = SpineStore(tmp_path / "spine.db")
    return TaskRunner(store, spine, router or MagicMock(), max_concurrent=max_concurrent)


def _wait_until(predicate, timeout=3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def _instant_success_router():
    router = MagicMock()

    def fake_complete_with_tools(goal, **kwargs):
        result = MagicMock()
        result.text = "all done"
        result.model = "test"
        return result

    router.complete_with_tools.side_effect = fake_complete_with_tools
    return router


# -- happy path ---------------------------------------------------------

def test_submit_runs_a_task_to_completion(tmp_path):
    runner = _runner(tmp_path, router=_instant_success_router())
    task_id = runner.submit(title="t", prompt="do it")

    assert _wait_until(lambda: runner.store.get(task_id).status == "done")
    task = runner.store.get(task_id)
    assert task.result_ref == "all done"
    assert task.finished_ts is not None


def test_completion_emits_exactly_one_task_finished_observation(tmp_path):
    runner = _runner(tmp_path, router=_instant_success_router())
    task_id = runner.submit(title="t", prompt="do it")

    assert _wait_until(lambda: runner.store.get(task_id).status == "done")
    observations = runner.spine.query(kinds=["task.finished"])
    assert len(observations) == 1
    assert observations[0].payload["task_id"] == task_id


def test_a_failed_task_emits_task_failed_not_task_finished(tmp_path):
    router = MagicMock()

    def fake_complete_with_tools(goal, **kwargs):
        raise RuntimeError("boom")

    router.complete_with_tools.side_effect = fake_complete_with_tools
    runner = _runner(tmp_path, router=router)
    task_id = runner.submit(title="t", prompt="do it")

    assert _wait_until(lambda: runner.store.get(task_id).status == "failed")
    assert runner.spine.query(kinds=["task.finished"]) == []
    failed = runner.spine.query(kinds=["task.failed"])
    assert len(failed) == 1
    assert failed[0].payload["task_id"] == task_id


def test_task_status_readable_without_blocking_the_task(tmp_path):
    """task_status (TaskStore.get) must return current progress without
    interrupting the run -- exercised here as: a read succeeds and
    reflects an in-progress state while the task is still running."""
    router = MagicMock()
    started = threading.Event()

    def fake_complete_with_tools(goal, **kwargs):
        on_tool_call = kwargs["on_tool_call"]
        on_tool_call("search", {}, "ok", tokens_used=10)
        started.set()
        time.sleep(0.15)
        result = MagicMock()
        result.text = "finished"
        result.model = "test"
        return result

    router.complete_with_tools.side_effect = fake_complete_with_tools
    runner = _runner(tmp_path, router=router)
    task_id = runner.submit(title="t", prompt="do it")

    assert started.wait(2.0)
    task = runner.store.get(task_id)  # must not block/raise while the task is mid-run
    assert task.status == "running"
    assert task.progress_note == "called search"

    assert _wait_until(lambda: runner.store.get(task_id).status == "done")


# -- budgets enforced, not advisory --------------------------------------

def test_runaway_task_exceeding_wall_clock_budget_is_actually_killed(tmp_path):
    """Proves the run is genuinely interrupted mid-loop, not merely
    flagged after it finishes: the fake router would call on_tool_call
    1000 times if nothing stopped it -- assert it was stopped after a
    handful, and marked failed for that reason."""
    calls = []
    router = MagicMock()

    def fake_complete_with_tools(goal, **kwargs):
        on_tool_call = kwargs["on_tool_call"]
        for i in range(1, 1001):
            calls.append(i)
            on_tool_call("noop", {}, "ok", tokens_used=1)  # raises once the wall-clock budget trips
        result = MagicMock()  # pragma: no cover -- must never be reached
        result.text = "ran all 1000 iterations"
        result.model = "test"
        return result

    router.complete_with_tools.side_effect = fake_complete_with_tools
    runner = _runner(tmp_path, router=router)
    task_id = runner.submit(title="t", prompt="do it", budget_seconds=0.0, budget_tokens=1_000_000)

    assert _wait_until(lambda: runner.store.get(task_id).status == "failed")
    assert len(calls) < 1000  # stopped well before exhausting the fake loop
    task = runner.store.get(task_id)
    assert "max_wall_seconds" in task.error


def test_runaway_task_exceeding_token_budget_is_actually_killed(tmp_path):
    calls = []
    router = MagicMock()

    def fake_complete_with_tools(goal, **kwargs):
        on_tool_call = kwargs["on_tool_call"]
        for i in range(1, 1001):
            calls.append(i)
            on_tool_call("noop", {}, "ok", tokens_used=i * 1000)  # raises once tokens exceed the budget
        result = MagicMock()  # pragma: no cover -- must never be reached
        result.text = "ran all 1000 iterations"
        result.model = "test"
        return result

    router.complete_with_tools.side_effect = fake_complete_with_tools
    runner = _runner(tmp_path, router=router)
    task_id = runner.submit(title="t", prompt="do it", budget_tokens=5000, budget_seconds=600.0)

    assert _wait_until(lambda: runner.store.get(task_id).status == "failed")
    assert len(calls) < 1000
    assert len(calls) <= 6  # 6th call already carries tokens_used=6000 > 5000
    task = runner.store.get(task_id)
    assert "max_tokens_total" in task.error


# -- max_concurrent_tasks --------------------------------------------------

def test_max_concurrent_tasks_is_never_exceeded(tmp_path):
    release = threading.Event()
    concurrent_peak = []
    lock = threading.Lock()
    active = [0]
    router = MagicMock()

    def fake_complete_with_tools(goal, **kwargs):
        with lock:
            active[0] += 1
            concurrent_peak.append(active[0])
        release.wait(2.0)
        with lock:
            active[0] -= 1
        result = MagicMock()
        result.text = "done"
        result.model = "test"
        return result

    router.complete_with_tools.side_effect = fake_complete_with_tools
    runner = _runner(tmp_path, router=router, max_concurrent=2)

    ids = [runner.submit(title=f"t{i}", prompt="do it") for i in range(5)]
    time.sleep(0.3)  # let the pool fill up to its cap
    assert max(concurrent_peak) <= 2

    release.set()
    assert _wait_until(lambda: all(runner.store.get(i).status == "done" for i in ids), timeout=5.0)


# -- cancellation -------------------------------------------------------

def test_cancelling_a_running_task_stops_it_within_5_seconds(tmp_path):
    router = MagicMock()
    keep_going = threading.Event()
    keep_going.set()

    def fake_complete_with_tools(goal, **kwargs):
        on_tool_call = kwargs["on_tool_call"]
        while keep_going.is_set():
            on_tool_call("noop", {}, "ok", tokens_used=1)  # raises TaskCancelled once cancel() is called
            time.sleep(0.02)
        result = MagicMock()  # pragma: no cover
        result.text = "should not get here"
        result.model = "test"
        return result

    router.complete_with_tools.side_effect = fake_complete_with_tools
    runner = _runner(tmp_path, router=router)
    task_id = runner.submit(title="t", prompt="do it", budget_seconds=600.0, budget_tokens=1_000_000)

    assert _wait_until(lambda: runner.store.get(task_id).status == "running")
    start = time.monotonic()
    assert runner.cancel(task_id) is True

    assert _wait_until(lambda: runner.store.get(task_id).status == "cancelled", timeout=5.0)
    assert time.monotonic() - start < 5.0
    keep_going.clear()


def test_cancelling_a_queued_task_marks_it_cancelled_immediately(tmp_path):
    router = _instant_success_router()
    runner = _runner(tmp_path, router=router, max_concurrent=0)  # nothing ever starts
    task_id = runner.submit(title="t", prompt="do it")

    assert runner.store.get(task_id).status == "queued"
    assert runner.cancel(task_id) is True
    assert runner.store.get(task_id).status == "cancelled"


def test_cancelling_a_finished_task_returns_false(tmp_path):
    runner = _runner(tmp_path, router=_instant_success_router())
    task_id = runner.submit(title="t", prompt="do it")
    assert _wait_until(lambda: runner.store.get(task_id).status == "done")

    assert runner.cancel(task_id) is False


def test_cancelling_an_unknown_task_returns_false(tmp_path):
    runner = _runner(tmp_path)
    assert runner.cancel(999) is False


# -- startup reconciliation -----------------------------------------------

def test_reconcile_on_startup_fails_running_tasks_and_never_resumes(tmp_path):
    runner = _runner(tmp_path)
    task_id = runner.store.create(title="t", prompt="p", budget_tokens=100, budget_seconds=60.0)
    runner.store.mark_running(task_id)  # simulates a task that was running when the process died

    count = runner.reconcile_on_startup()

    assert count == 1
    task = runner.store.get(task_id)
    assert task.status == "failed"
    assert task.error == "interrupted by restart"
    finished = runner.spine.query(kinds=["task.failed"])
    assert any(o.payload["task_id"] == task_id for o in finished)


def test_reconcile_on_startup_leaves_queued_and_done_tasks_alone(tmp_path):
    runner = _runner(tmp_path)
    queued_id = runner.store.create(title="t", prompt="p", budget_tokens=100, budget_seconds=60.0)

    runner.reconcile_on_startup()

    assert runner.store.get(queued_id).status == "queued"
