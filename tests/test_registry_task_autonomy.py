from unittest.mock import MagicMock

from argus.tools.base import PermissionTier, Tool
from argus.tools.registry import ToolRegistry


def _tool(name="click", repeatable=False, group=None, handler=None):
    return Tool(
        name=name, description="d", input_schema={"type": "object", "properties": {}},
        tier=PermissionTier.CONFIRM, handler=handler or (lambda args: "done"), repeatable=repeatable, group=group,
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


def test_explicit_user_request_skips_redundant_confirmation():
    confirmer = MagicMock(return_value=True)
    registry = ToolRegistry(confirmer=confirmer)
    handler = MagicMock(return_value="done")
    registry.register(_tool(name="capture_camera", handler=handler, repeatable=True))

    registry.reset_task_autonomy(explicitly_requested=True)
    result = registry.execute("capture_camera", {})

    assert result == "done"
    confirmer.assert_not_called()
    handler.assert_called_once_with({})


def test_different_repeatable_tools_confirm_independently_when_ungrouped():
    confirmer = MagicMock(return_value=True)
    registry = ToolRegistry(confirmer=confirmer)
    registry.register(_tool(name="click", repeatable=True))
    registry.register(_tool(name="type_text", repeatable=True))

    registry.execute("click", {})
    registry.execute("type_text", {})
    registry.execute("click", {})
    registry.execute("type_text", {})

    assert confirmer.call_count == 2


def test_grouped_tools_share_one_approval():
    """Confirmed live as a further gap even after per-tool repeatable
    shipped: "open my calculator and add 4+4" is one explicit instruction
    naming multiple actions (open_app, then several clicks), but each
    distinct tool name still asked once on its own."""
    confirmer = MagicMock(return_value=True)
    registry = ToolRegistry(confirmer=confirmer)
    registry.register(_tool(name="open_app", repeatable=True, group="desktop_control"))
    registry.register(_tool(name="click", repeatable=True, group="desktop_control"))
    registry.register(_tool(name="type_text", repeatable=True, group="desktop_control"))

    registry.execute("open_app", {})
    registry.execute("click", {})
    registry.execute("click", {})
    registry.execute("type_text", {})

    assert confirmer.call_count == 1


def test_grouped_approval_still_resets_per_task():
    confirmer = MagicMock(return_value=True)
    registry = ToolRegistry(confirmer=confirmer)
    registry.register(_tool(name="open_app", repeatable=True, group="desktop_control"))
    registry.register(_tool(name="click", repeatable=True, group="desktop_control"))

    registry.execute("open_app", {})
    registry.execute("click", {})
    registry.reset_task_autonomy()
    registry.execute("click", {})

    assert confirmer.call_count == 2


def test_ungrouped_and_grouped_tools_can_coexist():
    confirmer = MagicMock(return_value=True)
    registry = ToolRegistry(confirmer=confirmer)
    registry.register(_tool(name="capture_camera", repeatable=False))  # deliberately not grouped
    registry.register(_tool(name="click", repeatable=True, group="desktop_control"))

    registry.execute("click", {})
    registry.execute("click", {})
    registry.execute("capture_camera", {})
    registry.execute("capture_camera", {})

    assert confirmer.call_count == 3  # 1 for the click group, 2 for camera (never grouped/repeatable)
