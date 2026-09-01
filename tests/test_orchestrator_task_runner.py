from argus.orchestrator import Orchestrator


def test_task_runner_absent_by_default(monkeypatch):
    monkeypatch.setattr("argus.orchestrator.settings.enable_task_runner", False)
    orch = Orchestrator()
    assert orch.task_runner is None
    assert "start_task" not in orch.tools._tools


def test_task_runner_constructed_when_enabled(monkeypatch):
    monkeypatch.setattr("argus.orchestrator.settings.enable_task_runner", True)
    orch = Orchestrator()
    assert orch.task_runner is not None
    assert "start_task" in orch.tools._tools
    assert "task_status" in orch.tools._tools
    assert "cancel_task" in orch.tools._tools


def test_explicit_tool_registry_bypasses_task_tool_registration(monkeypatch):
    """Same as the router precedent: a caller-supplied registry is used
    as-is, not augmented -- task_runner is still constructed and
    reachable, just not wired into a registry this call didn't build."""
    from argus.tools.registry import ToolRegistry

    monkeypatch.setattr("argus.orchestrator.settings.enable_task_runner", True)
    custom = ToolRegistry()
    orch = Orchestrator(tool_registry=custom)

    assert orch.tools is custom
    assert orch.task_runner is not None
