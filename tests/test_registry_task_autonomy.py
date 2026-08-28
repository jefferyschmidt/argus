from unittest.mock import MagicMock

from argus.tools.base import PermissionTier, Tool
from argus.tools.registry import ToolRegistry


def _tool(name="click", repeatable=False):
    return Tool(
        name=name, description="d", input_schema={"type": "object", "properties": {}},
        tier=PermissionTier.CONFIRM, handler=lambda args: "done", repeatable=repeatable,
    )


def test_repeatable_tool_only_confirms_once_per_task():
    """Confirmed live as a real usability gap: desktop automation asking to
    confirm every single click made multi-step tasks unusable."""
    confirmer = MagicMock(return_value=True)
    registry = ToolRegistry(confirmer=confirmer)
    registry.register(_tool(repeatable=True))

    for _ in range(5):
        registry.execute("click", {})

    assert confirmer.call_count == 1


def test_non_repeatable_tool_still_confirms_every_time():
    confirmer = MagicMock(return_value=True)
    registry = ToolRegistry(confirmer=confirmer)
    registry.register(_tool(repeatable=False))

    for _ in range(3):
        registry.execute("click", {})

    assert confirmer.call_count == 3


def test_reset_task_autonomy_clears_approval_for_a_new_task():
    confirmer = MagicMock(return_value=True)
    registry = ToolRegistry(confirmer=confirmer)
    registry.register(_tool(repeatable=True))

    registry.execute("click", {})
    registry.reset_task_autonomy()
    registry.execute("click", {})

    assert confirmer.call_count == 2


def test_different_repeatable_tools_confirm_independently():
    confirmer = MagicMock(return_value=True)
    registry = ToolRegistry(confirmer=confirmer)
    registry.register(_tool(name="click", repeatable=True))
    registry.register(_tool(name="type_text", repeatable=True))

    registry.execute("click", {})
    registry.execute("type_text", {})
    registry.execute("click", {})
    registry.execute("type_text", {})

    assert confirmer.call_count == 2
