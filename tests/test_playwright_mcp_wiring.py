from unittest.mock import MagicMock, patch

from argus.tools import build_default_registry
from argus.tools.base import PermissionTier, Tool


def test_playwright_tools_absent_by_default():
    """enable_playwright_mcp defaults to False -- launching a real Node/
    browser subprocess at every Argus startup for a capability most turns
    never use isn't worth the cost."""
    with patch("argus.tools.settings.enable_playwright_mcp", False):
        registry = build_default_registry()

    assert not any(name.startswith("playwright_") for name in registry._tools)


def test_playwright_tools_registered_when_enabled():
    fake_tool = Tool(
        name="playwright_browser_navigate",
        description="Navigate to a URL",
        input_schema={"type": "object", "properties": {}},
        tier=PermissionTier.CONFIRM,
        handler=lambda args: "ok",
        repeatable=True,
        group="playwright_mcp",
    )
    fake_bridge = MagicMock()
    fake_bridge.build_tools.return_value = [fake_tool]

    with patch("argus.tools.settings.enable_playwright_mcp", True), \
         patch("argus.mcp_bridge.McpServerBridge", return_value=fake_bridge):
        registry = build_default_registry()

    assert "playwright_browser_navigate" in registry._tools
    fake_bridge.build_tools.assert_called_once_with(name_prefix="playwright_", group="playwright_mcp")


def test_playwright_startup_failure_does_not_break_the_rest_of_the_registry():
    """A missing/broken npx, or the server failing to start, must not take
    down every other tool Argus has -- same "skip and warn" spirit as the
    plugin loader."""
    with patch("argus.tools.settings.enable_playwright_mcp", True), \
         patch("argus.mcp_bridge.McpServerBridge", side_effect=RuntimeError("npx not found")):
        registry = build_default_registry()

    assert "read_file" in registry._tools
    assert not any(name.startswith("playwright_") for name in registry._tools)
