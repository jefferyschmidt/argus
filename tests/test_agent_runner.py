from unittest.mock import MagicMock

from argus.agent.runner import AgentRunner
from argus.tools.base import PermissionTier, Tool
from argus.tools.registry import ToolRegistry


def _registry():
    registry = ToolRegistry()
    registry.register(Tool(
        name="noop", description="d", input_schema={"type": "object", "properties": {}},
        tier=PermissionTier.ALLOW, handler=lambda args: "ok",
    ))
    return registry


def _runner(tmp_path, monkeypatch, **kwargs):
    monkeypatch.setattr("argus.agent.runner.settings.argus_data_dir", str(tmp_path))
    router = MagicMock()
    return AgentRunner(tool_registry=_registry(), router=router, **kwargs), router


def test_token_budget_stops_a_runaway_task(tmp_path, monkeypatch):
    """A task exceeding budget_tokens is actually stopped mid-run, not
    merely flagged after the fact -- the router never gets to complete."""
    runner, router = _runner(tmp_path, monkeypatch, max_tokens_total=100)

    def fake_complete_with_tools(goal, **kwargs):
        # Simulates the real AnthropicClient loop calling on_tool_call
        # with a token count that blows the budget on the first call.
        kwargs["on_tool_call"]("noop", {}, "ok", tokens_used=500)
        raise AssertionError("must never reach a second tool call or a normal return")

    router.complete_with_tools.side_effect = fake_complete_with_tools

    result = runner.run("do something expensive")

    assert "Stopped" in result
    assert "max_tokens_total" in result


def test_wall_clock_budget_still_stops_a_runaway_task(tmp_path, monkeypatch):
    runner, router = _runner(tmp_path, monkeypatch, max_wall_seconds=0.0)

    def fake_complete_with_tools(goal, **kwargs):
        kwargs["on_tool_call"]("noop", {}, "ok", tokens_used=1)
        raise AssertionError("must never reach a second tool call or a normal return")

    router.complete_with_tools.side_effect = fake_complete_with_tools

    result = runner.run("do something slow")

    assert "Stopped" in result
    assert "max_wall_seconds" in result


def test_no_token_budget_means_no_cap(tmp_path, monkeypatch):
    """max_tokens_total=None (the default) -- existing callers like the
    `argus agent` CLI must be unaffected."""
    runner, router = _runner(tmp_path, monkeypatch)  # no max_tokens_total

    def fake_complete_with_tools(goal, **kwargs):
        kwargs["on_tool_call"]("noop", {}, "ok", tokens_used=10_000_000)
        result = MagicMock()
        result.text = "done"
        result.model = "test"
        return result

    router.complete_with_tools.side_effect = fake_complete_with_tools

    result = runner.run("do something")
    assert result == "done"


def test_on_progress_fires_after_every_tool_call(tmp_path, monkeypatch):
    runner, router = _runner(tmp_path, monkeypatch)
    progress_notes = []

    def fake_complete_with_tools(goal, **kwargs):
        kwargs["on_tool_call"]("noop", {}, "ok", tokens_used=1)
        result = MagicMock()
        result.text = "done"
        result.model = "test"
        return result

    router.complete_with_tools.side_effect = fake_complete_with_tools

    runner.run("do something", on_progress=progress_notes.append)

    assert progress_notes == ["called noop"]
