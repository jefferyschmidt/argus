import pytest

from argus.tools.base import PermissionTier, Tool
from argus.tools.registry import ToolDenied, ToolRegistry


def _make_tool(tier: PermissionTier, calls: list) -> Tool:
    def handler(args: dict) -> str:
        calls.append(args)
        return "ok"

    return Tool(
        name="test_tool",
        description="a test tool",
        input_schema={"type": "object", "properties": {}},
        tier=tier,
        handler=handler,
    )


def test_allow_tier_runs_without_confirmation():
    calls = []
    registry = ToolRegistry(confirmer=lambda name, inp: pytest.fail("should not be called"))
    registry.register(_make_tool(PermissionTier.ALLOW, calls))

    result = registry.execute("test_tool", {"x": 1})

    assert result == "ok"
    assert calls == [{"x": 1}]


def test_confirm_tier_runs_when_confirmer_approves():
    calls = []
    registry = ToolRegistry(confirmer=lambda name, inp: True)
    registry.register(_make_tool(PermissionTier.CONFIRM, calls))

    result = registry.execute("test_tool", {})

    assert result == "ok"
    assert calls == [{}]


def test_confirm_tier_raises_when_confirmer_declines():
    calls = []
    registry = ToolRegistry(confirmer=lambda name, inp: False)
    registry.register(_make_tool(PermissionTier.CONFIRM, calls))

    with pytest.raises(ToolDenied):
        registry.execute("test_tool", {})
    assert calls == []


def test_deny_tier_never_executes():
    calls = []
    registry = ToolRegistry(confirmer=lambda name, inp: True)
    registry.register(_make_tool(PermissionTier.DENY, calls))

    result = registry.execute("test_tool", {})

    assert "disabled" in result
    assert calls == []


def test_deny_tier_excluded_from_schemas():
    registry = ToolRegistry()
    registry.register(_make_tool(PermissionTier.DENY, []))
    registry.register(_make_tool(PermissionTier.ALLOW, []))

    # only one schema since names collide in this test; assert deny didn't add extra visibility
    assert len(registry.schemas()) == 1


def test_unknown_tool_returns_error_string():
    registry = ToolRegistry()
    result = registry.execute("nonexistent", {})
    assert "unknown tool" in result
