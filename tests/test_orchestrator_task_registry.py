"""PRD.md §19 unit 39: Orchestrator previously constructed TaskRunner with
no tool_registry at all (TaskRunner(TaskStore(), self.spine, self.router)),
so every task ran through AgentRunner's OWN build_default_registry() --
bare, with none of router/rule_store/decision_log/spine passed in. No task
tools, no rule introspection, no compose_document/query_timeline, and
critically no AuthorizationChecker (rule_store=None), so a standing
authorization never applied inside a task. The same "orphaned registry"
bug unit 33 already fixed for RealtimeVoiceLoop, still present here at the
task site until this unit."""

from unittest.mock import MagicMock, patch

import pytest

from argus.orchestrator import Orchestrator


@pytest.fixture
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr("argus.config.settings.argus_data_dir", str(tmp_path))
    monkeypatch.setattr("argus.config.settings.spine_db_path", str(tmp_path / "spine.db"))
    monkeypatch.setattr("argus.config.settings.enable_task_runner", True)


def test_task_runner_shares_the_orchestrators_registry_not_a_bare_one(_isolated):
    orch = Orchestrator()

    assert orch.task_runner is not None
    assert orch.task_runner.tool_registry is orch.tools


def test_exactly_one_registry_is_built_for_orchestrator_and_task_runner(_isolated):
    from argus.tools import build_default_registry as real_build

    calls = []

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return real_build(*args, **kwargs)

    with patch("argus.orchestrator.build_default_registry", side_effect=spy):
        orch = Orchestrator()

    assert len(calls) == 1
    assert orch.task_runner.tool_registry is orch.tools


def test_task_tools_are_registered_onto_the_shared_registry(_isolated):
    orch = Orchestrator()

    assert "start_task" in orch.tools._tools
    assert "task_status" in orch.tools._tools
    assert "cancel_task" in orch.tools._tools


def test_task_runner_disabled_builds_no_task_tools_and_still_one_registry(tmp_path, monkeypatch):
    monkeypatch.setattr("argus.config.settings.argus_data_dir", str(tmp_path))
    monkeypatch.setattr("argus.config.settings.spine_db_path", str(tmp_path / "spine.db"))
    monkeypatch.setattr("argus.config.settings.enable_task_runner", False)
    from argus.tools import build_default_registry as real_build

    calls = []

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return real_build(*args, **kwargs)

    with patch("argus.orchestrator.build_default_registry", side_effect=spy):
        orch = Orchestrator()

    assert orch.task_runner is None
    assert len(calls) == 1
    assert "start_task" not in orch.tools._tools


def test_caller_supplied_registry_is_not_mutated_with_task_tools(tmp_path, monkeypatch):
    """When a caller passes its own tool_registry (e.g. a test double),
    Orchestrator must not silently register tools onto it -- that
    registry's contents are the caller's call, not this constructor's."""
    monkeypatch.setattr("argus.config.settings.argus_data_dir", str(tmp_path))
    monkeypatch.setattr("argus.config.settings.spine_db_path", str(tmp_path / "spine.db"))
    monkeypatch.setattr("argus.config.settings.enable_task_runner", True)
    from argus.tools.registry import ToolRegistry

    supplied = ToolRegistry()
    orch = Orchestrator(tool_registry=supplied)

    assert orch.tools is supplied
    assert orch.task_runner.tool_registry is supplied
    assert "start_task" not in supplied._tools


def test_standing_authorization_applies_through_the_task_runners_own_registry(_isolated):
    """The point of unit 39, not just object identity: a standing
    authorization grant, proposed through the normal rule_store path,
    must auto-approve a CONFIRM-tier call made through
    task_runner.tool_registry -- proving that registry actually has the
    AuthorizationChecker wired, the same as the foreground conversation's
    registry does."""
    import time

    from argus.tools.base import PermissionTier, Tool

    orch = Orchestrator()
    orch.tools.register(Tool(
        name="delete_email", description="test", input_schema={"type": "object", "properties": {}},
        tier=PermissionTier.CONFIRM, handler=lambda args: "deleted",
    ))
    rule_id = orch.rule_store.propose(
        natural_language="always delete newsletters", kind="authorization", trigger={}, action={"type": "authorize"},
        authorization={
            "tool": "delete_email",
            "allow": [{"field": "sender", "op": "contains", "value": "newsletter"}],
            "deny": [],
            "expires_ts": time.time() + 86400,
        },
    )
    orch.rule_store.confirm(rule_id)

    confirmer = MagicMock(return_value=True)
    orch.task_runner.tool_registry.confirmer = confirmer

    result = orch.task_runner.tool_registry.execute("delete_email", {"sender": "newsletter@example.com"})

    assert result == "deleted"
    confirmer.assert_not_called()  # auto-approved by the standing grant, not asked
    auto_approved = orch.spine.query(kinds=["tool.auto_approved"])
    assert len(auto_approved) == 1
    assert auto_approved[0].payload["tool"] == "delete_email"
