"""PRD.md §6. start_task (CONFIRM -- it spends money), task_status,
cancel_task (ALLOW). All three close over one shared TaskRunner instance
-- constructing a fresh one per call would lose the worker pool's
concurrency bookkeeping (which tasks are currently running)."""

from argus.tools.base import PermissionTier, Tool


def _describe(task) -> str:
    bits = [f"#{task.id} '{task.title}' [{task.status}]"]
    if task.progress_note:
        bits.append(f"progress: {task.progress_note}")
    if task.status == "done" and task.result_ref:
        bits.append(f"result: {task.result_ref}")
    if task.status == "failed" and task.error:
        bits.append(f"error: {task.error}")
    return " -- ".join(bits)


def _build_start_task(task_runner) -> Tool:
    def handler(args: dict) -> str:
        task_id = task_runner.submit(
            title=args["title"], prompt=args["prompt"],
            budget_tokens=args.get("budget_tokens"), budget_seconds=args.get("budget_seconds"),
            created_by="user",
        )
        return f"Task #{task_id} '{args['title']}' queued."

    return Tool(
        name="start_task",
        description=(
            "Starts a task that runs autonomously in the background over minutes or "
            "hours -- for something too long or expensive to do inline in this "
            "conversation. Argus works through it with tools, up to its budget, and "
            "never announces itself when done -- check back with task_status, or just "
            "ask later. Spends real money (LLM usage), so this requires confirmation."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short label for the task."},
                "prompt": {"type": "string", "description": "The goal, in full -- same as an agent-mode instruction."},
                "budget_tokens": {"type": "integer", "description": "Optional token cap; a sensible default is used if omitted."},
                "budget_seconds": {"type": "number", "description": "Optional wall-clock cap in seconds; a sensible default is used if omitted."},
            },
            "required": ["title", "prompt"],
        },
        tier=PermissionTier.CONFIRM,
        handler=handler,
    )


def _build_task_status(task_runner) -> Tool:
    def handler(args: dict) -> str:
        task_id = args.get("task_id")
        if task_id is not None:
            task = task_runner.store.get(task_id)
            return _describe(task) if task else f"No task #{task_id}."
        tasks = task_runner.store.list_all(limit=20)
        return "\n".join(_describe(t) for t in tasks) if tasks else "No tasks yet."

    return Tool(
        name="task_status",
        description="Checks progress on one autonomous task (by id) or lists recent ones if no id is given.",
        input_schema={
            "type": "object",
            "properties": {"task_id": {"type": "integer", "description": "Optional -- omit to list recent tasks."}},
        },
        tier=PermissionTier.ALLOW,
        handler=handler,
    )


def _build_cancel_task(task_runner) -> Tool:
    def handler(args: dict) -> str:
        task_id = args["task_id"]
        if task_runner.cancel(task_id):
            return f"Task #{task_id} cancelled."
        return f"Task #{task_id} couldn't be cancelled (not found, or already finished)."

    return Tool(
        name="cancel_task",
        description="Cancels a queued or running autonomous task by id.",
        input_schema={
            "type": "object",
            "properties": {"task_id": {"type": "integer", "description": "The task's ID (from task_status or start_task)."}},
            "required": ["task_id"],
        },
        tier=PermissionTier.ALLOW,
        handler=handler,
    )
