from unittest.mock import MagicMock

from argus.tools import build_default_registry


def test_task_tools_absent_when_disabled(monkeypatch):
    monkeypatch.setattr("argus.tools.settings.enable_task_runner", False)
    registry = build_default_registry(router=None, task_runner=MagicMock())

    assert "start_task" not in registry._tools
    assert "task_status" not in registry._tools
    assert "cancel_task" not in registry._tools


def test_task_tools_absent_when_enabled_but_no_task_runner(monkeypatch):
    monkeypatch.setattr("argus.tools.settings.enable_task_runner", True)
    registry = build_default_registry(router=None, task_runner=None)

    assert "start_task" not in registry._tools


def test_task_tools_present_when_enabled_with_a_task_runner(monkeypatch):
    monkeypatch.setattr("argus.tools.settings.enable_task_runner", True)
    registry = build_default_registry(router=None, task_runner=MagicMock())

    assert "start_task" in registry._tools
    assert "task_status" in registry._tools
    assert "cancel_task" in registry._tools
