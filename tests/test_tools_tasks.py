from unittest.mock import MagicMock

from argus.tasks.store import Task
from argus.tools.base import PermissionTier
from argus.tools.tasks import _build_cancel_task, _build_start_task, _build_task_status


def _task(**overrides) -> Task:
    defaults = dict(
        id=1, title="Research X", prompt="go research X", status="running",
        created_ts=0.0, started_ts=0.0, finished_ts=None, progress_note=None,
        result_ref=None, error=None, created_by="user", budget_tokens=1000, budget_seconds=60.0, tokens_used=0,
    )
    defaults.update(overrides)
    return Task(**defaults)


def test_start_task_is_confirm_tier():
    tool = _build_start_task(MagicMock())
    assert tool.tier == PermissionTier.CONFIRM


def test_task_status_and_cancel_task_are_allow_tier():
    assert _build_task_status(MagicMock()).tier == PermissionTier.ALLOW
    assert _build_cancel_task(MagicMock()).tier == PermissionTier.ALLOW


def test_start_task_submits_and_reports_the_id():
    runner = MagicMock()
    runner.submit.return_value = 42
    tool = _build_start_task(runner)

    result = tool.handler({"title": "Research X", "prompt": "go research X"})

    runner.submit.assert_called_once_with(
        title="Research X", prompt="go research X", budget_tokens=None, budget_seconds=None, created_by="user",
    )
    assert "42" in result
    assert "Research X" in result


def test_start_task_forwards_optional_budgets():
    runner = MagicMock()
    runner.submit.return_value = 1
    tool = _build_start_task(runner)

    tool.handler({"title": "t", "prompt": "p", "budget_tokens": 5000, "budget_seconds": 120.0})

    runner.submit.assert_called_once_with(
        title="t", prompt="p", budget_tokens=5000, budget_seconds=120.0, created_by="user",
    )


def test_task_status_with_id_describes_one_task():
    runner = MagicMock()
    runner.store.get.return_value = _task(progress_note="called search_web")
    tool = _build_task_status(runner)

    result = tool.handler({"task_id": 1})

    runner.store.get.assert_called_once_with(1)
    assert "#1" in result
    assert "running" in result
    assert "called search_web" in result


def test_task_status_with_unknown_id_says_so():
    runner = MagicMock()
    runner.store.get.return_value = None
    tool = _build_task_status(runner)

    result = tool.handler({"task_id": 999})
    assert "No task #999" in result


def test_task_status_without_id_lists_recent():
    runner = MagicMock()
    runner.store.list_all.return_value = [_task(id=1), _task(id=2, title="Other")]
    tool = _build_task_status(runner)

    result = tool.handler({})

    runner.store.list_all.assert_called_once_with(limit=20)
    assert "#1" in result
    assert "#2" in result


def test_task_status_without_id_and_no_tasks():
    runner = MagicMock()
    runner.store.list_all.return_value = []
    tool = _build_task_status(runner)

    assert tool.handler({}) == "No tasks yet."


def test_task_status_shows_result_when_done():
    runner = MagicMock()
    runner.store.get.return_value = _task(status="done", result_ref="found 3 papers")
    tool = _build_task_status(runner)

    result = tool.handler({"task_id": 1})
    assert "found 3 papers" in result


def test_task_status_shows_error_when_failed():
    runner = MagicMock()
    runner.store.get.return_value = _task(status="failed", error="exceeded max_wall_seconds")
    tool = _build_task_status(runner)

    result = tool.handler({"task_id": 1})
    assert "exceeded max_wall_seconds" in result


def test_cancel_task_success():
    runner = MagicMock()
    runner.cancel.return_value = True
    tool = _build_cancel_task(runner)

    result = tool.handler({"task_id": 1})

    runner.cancel.assert_called_once_with(1)
    assert "cancelled" in result.lower()


def test_cancel_task_failure():
    runner = MagicMock()
    runner.cancel.return_value = False
    tool = _build_cancel_task(runner)

    result = tool.handler({"task_id": 1})
    assert "couldn't be cancelled" in result
