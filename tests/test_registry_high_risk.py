from unittest.mock import MagicMock

from argus.tools.base import PermissionTier, Tool
from argus.tools.registry import ToolDenied, ToolRegistry


def _tool(name="risky_tool", tier=PermissionTier.CONFIRM, high_risk=False):
    return Tool(
        name=name, description="d", input_schema={"type": "object", "properties": {}},
        tier=tier, handler=lambda args: "done", high_risk=high_risk,
    )


def test_normal_confirm_tool_only_asks_once():
    confirmer = MagicMock(return_value=True)
    registry = ToolRegistry(confirmer=confirmer)
    registry.register(_tool(high_risk=False))

    result = registry.execute("risky_tool", {})

    assert result == "done"
    assert confirmer.call_count == 1


def test_high_risk_tool_asks_twice_and_both_must_pass():
    confirmer = MagicMock(return_value=True)
    registry = ToolRegistry(confirmer=confirmer)
    registry.register(_tool(high_risk=True))

    result = registry.execute("risky_tool", {})

    assert result == "done"
    assert confirmer.call_count == 2


def test_high_risk_tool_denied_on_first_confirmation_never_asks_twice():
    confirmer = MagicMock(return_value=False)
    registry = ToolRegistry(confirmer=confirmer)
    registry.register(_tool(high_risk=True))

    try:
        registry.execute("risky_tool", {})
        assert False, "expected ToolDenied"
    except ToolDenied:
        pass
    assert confirmer.call_count == 1  # never got to a pointless second ask


def test_high_risk_tool_denied_on_second_confirmation_does_not_execute():
    confirmer = MagicMock(side_effect=[True, False])  # yes, then no
    registry = ToolRegistry(confirmer=confirmer)
    registry.register(_tool(high_risk=True))

    try:
        registry.execute("risky_tool", {})
        assert False, "expected ToolDenied"
    except ToolDenied:
        pass
    assert confirmer.call_count == 2


def test_allow_tier_tool_never_calls_confirmer():
    confirmer = MagicMock(return_value=True)
    registry = ToolRegistry(confirmer=confirmer)
    registry.register(_tool(tier=PermissionTier.ALLOW, high_risk=True))  # high_risk irrelevant if not CONFIRM

    registry.execute("risky_tool", {})

    confirmer.assert_not_called()
