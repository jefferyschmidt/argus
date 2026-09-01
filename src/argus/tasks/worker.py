"""PRD.md §6. Bounded worker pool executing queued tasks by extending
agent/runner.py's AgentRunner (not duplicating its tool loop). A task
never announces itself -- completion only ever emits an observation onto
the spine; whether/when/how that gets surfaced is SalienceEngine's job
(Phase C), not this package's. Never pass speak_fn in here."""

import logging
import threading
import time

from argus.agent.runner import AgentRunner
from argus.config import settings
from argus.spine.observation import Observation
from argus.tasks.store import Task, TaskStore

log = logging.getLogger(__name__)


class TaskCancelled(Exception):
    pass


class TaskRunner:
    def __init__(
        self, store: TaskStore, spine, router, tool_registry=None, max_concurrent: int | None = None,
    ):
        self.store = store
        self.spine = spine
        self.router = router
        self.tool_registry = tool_registry  # None -> AgentRunner builds its own default registry
        self.max_concurrent = max_concurrent if max_concurrent is not None else settings.max_concurrent_tasks
        self._lock = threading.Lock()
        self._running_ids: set[int] = set()
        self._cancel_requested: set[int] = set()

    def reconcile_on_startup(self) -> int:
        """PRD §6: any task found `running` at startup becomes `failed`
        with reason "interrupted by restart" -- never auto-resumed. A
        half-finished agentic task resumed blind is worse than a clean
        failure the user can retry. Returns how many were reconciled."""
        reconciled = 0
        for task in self.store.list_running():
            if self.store.mark_failed(task.id, "interrupted by restart"):
                self._emit_finished(task.id, kind="task.failed")
                reconciled += 1
        return reconciled

    def submit(
        self, *, title: str, prompt: str, budget_tokens: int | None = None,
        budget_seconds: float | None = None, created_by: str = "user",
    ) -> int:
        task_id = self.store.create(
            title=title, prompt=prompt,
            budget_tokens=budget_tokens if budget_tokens is not None else settings.default_task_budget_tokens,
            budget_seconds=budget_seconds if budget_seconds is not None else settings.default_task_budget_seconds,
            created_by=created_by,
        )
        self._maybe_start_next()
        return task_id

    def cancel(self, task_id: int) -> bool:
        """Cancelling a running task stops it within a few seconds (PRD:
        within 5) -- the on_progress hook fires after every tool call and
        checks this flag, so the soonest possible stop is the next tool
        boundary, not an arbitrary poll interval."""
        task = self.store.get(task_id)
        if task is None:
            return False
        if task.status == "queued":
            return self.store.mark_cancelled(task_id)
        if task.status == "running":
            with self._lock:
                self._cancel_requested.add(task_id)
            return True
        return False

    def _maybe_start_next(self) -> None:
        with self._lock:
            if len(self._running_ids) >= self.max_concurrent:
                return
            queued = self.store.list_queued(limit=1)
            if not queued:
                return
            task = queued[0]
            if not self.store.mark_running(task.id):
                return  # lost a race to grab this task -- fine, try again next call
            self._running_ids.add(task.id)
        threading.Thread(target=self._execute, args=(task,), daemon=True).start()

    def _execute(self, task: Task) -> None:
        try:
            self._run_task(task)
        except Exception:
            # Backstop only -- _run_task() catches everything it can
            # attribute to the task itself and marks it failed. Anything
            # that still escapes must not take the whole pool down, nor
            # leave the task stuck 'running' forever.
            log.exception("Task #%s runner crashed outside its own error handling", task.id)
            self.store.mark_failed(task.id, "internal runner error")
            self._emit_finished(task.id, kind="task.failed")
        finally:
            with self._lock:
                self._running_ids.discard(task.id)
                self._cancel_requested.discard(task.id)
            self._maybe_start_next()

    def _run_task(self, task: Task) -> None:
        self._emit(kind="task.started", task_id=task.id, subject=task.title)

        def on_progress(note: str) -> None:
            self.store.update_progress(task.id, note)
            self._emit(kind="task.progress", task_id=task.id, subject=task.title, payload={"note": note})
            if task.id in self._cancel_requested:
                raise TaskCancelled(f"task #{task.id} cancelled by user")

        runner = AgentRunner(
            tool_registry=self.tool_registry, router=self.router,
            max_wall_seconds=task.budget_seconds, max_tokens_total=task.budget_tokens,
        )
        try:
            summary = runner.run(task.prompt, on_progress=on_progress)
        except TaskCancelled:
            summary = None

        if task.id in self._cancel_requested:
            self.store.mark_cancelled(task.id)
            # Not task.finished/task.failed -- a deliberate cancel isn't a
            # completion in either sense the spine vocabulary distinguishes.
            return
        if summary is not None and (summary.startswith("Stopped:") or summary.startswith("Agent run failed")):
            # AgentRunner.run() never raises -- it catches its own
            # AgentBudgetExceeded (wall-clock or token budget, "Stopped:
            # ...") and any other exception ("Agent run failed with an
            # error: ...") and returns a string either way. This is where
            # both become a hard, enforced task failure, not merely a
            # flag checked at the end.
            self.store.mark_failed(task.id, summary)
            self._emit_finished(task.id, kind="task.failed")
            return
        self.store.mark_done(task.id, result_ref=summary or "")
        self._emit_finished(task.id, kind="task.finished")

    def _emit(self, *, kind: str, task_id: int, subject: str | None = None, payload: dict | None = None) -> None:
        self.spine.record(Observation(
            source="tasks", kind=kind, ts=time.time(), subject=subject, payload={"task_id": task_id, **(payload or {})},
        ))

    def _emit_finished(self, task_id: int, *, kind: str) -> None:
        # dedupe_key: each task transitions to done/failed exactly once
        # (TaskStore's mark_done/mark_failed only succeed from 'running'),
        # so this can never double-fire for the same task -- but the key
        # makes that a guarantee the spine enforces too, not just this
        # code's own care (PRD §6 acceptance: "exactly one task.finished").
        self.spine.record(Observation(
            source="tasks", kind=kind, ts=time.time(), payload={"task_id": task_id},
            dedupe_key=f"{kind}:{task_id}",
        ))
