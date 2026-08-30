from unittest.mock import MagicMock, patch

from argus.tools import build_default_registry
from argus.tools.base import PermissionTier, Tool


def _fake_tool(name):
    return Tool(
        name=name,
        description="a tool",
        input_schema={"type": "object", "properties": {}},
        tier=PermissionTier.CONFIRM,
        handler=lambda args: "ok",
        repeatable=True,
        group="test",
    )


def test_github_tools_absent_by_default():
    with patch("argus.tools.settings.enable_github_mcp", False):
        registry = build_default_registry()

    assert not any(name.startswith("github_") for name in registry._tools)


def test_github_tools_registered_when_enabled():
    fake_bridge = MagicMock()
    fake_bridge.build_tools.return_value = [_fake_tool("github_create_issue")]

    with patch("argus.tools.settings.enable_github_mcp", True), \
         patch("argus.tools.settings.github_mcp_url", "https://api.githubcopilot.com/mcp/"), \
         patch("argus.tools.settings.github_mcp_token", "ghp_test"), \
         patch("argus.mcp_bridge.McpServerBridge", return_value=fake_bridge) as bridge_cls:
        registry = build_default_registry()

    assert "github_create_issue" in registry._tools
    bridge_cls.assert_called_once_with(
        url="https://api.githubcopilot.com/mcp/",
        headers={"Authorization": "Bearer ghp_test"},
    )


def test_github_startup_failure_does_not_break_the_rest_of_the_registry():
    with patch("argus.tools.settings.enable_github_mcp", True), \
         patch("argus.mcp_bridge.McpServerBridge", side_effect=RuntimeError("unauthorized")):
        registry = build_default_registry()

    assert "read_file" in registry._tools
    assert not any(name.startswith("github_") for name in registry._tools)


def test_figma_tools_absent_by_default():
    with patch("argus.tools.settings.enable_figma_mcp", False):
        registry = build_default_registry()

    assert not any(name.startswith("figma_") for name in registry._tools)


def test_figma_tools_registered_when_enabled():
    fake_bridge = MagicMock()
    fake_bridge.build_tools.return_value = [_fake_tool("figma_get_code")]

    with patch("argus.tools.settings.enable_figma_mcp", True), \
         patch("argus.tools.settings.figma_mcp_url", "http://127.0.0.1:3845/mcp"), \
         patch("argus.mcp_bridge.McpServerBridge", return_value=fake_bridge) as bridge_cls:
        registry = build_default_registry()

    assert "figma_get_code" in registry._tools
    bridge_cls.assert_called_once_with(url="http://127.0.0.1:3845/mcp")


def test_figma_startup_failure_does_not_break_the_rest_of_the_registry():
    """Confirmed as the expected common case, not just theoretical: this
    fails whenever the Figma desktop app isn't running with Dev Mode's MCP
    server enabled."""
    with patch("argus.tools.settings.enable_figma_mcp", True), \
         patch("argus.mcp_bridge.McpServerBridge", side_effect=RuntimeError("connection refused")):
        registry = build_default_registry()

    assert "read_file" in registry._tools
    assert not any(name.startswith("figma_") for name in registry._tools)
