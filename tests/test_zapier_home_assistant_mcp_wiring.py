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


def test_zapier_tools_absent_without_a_configured_url():
    with patch("argus.tools.settings.zapier_mcp_url", ""):
        registry = build_default_registry()

    assert not any(name.startswith("zapier_") for name in registry._tools)


def test_zapier_tools_registered_when_url_is_set():
    fake_bridge = MagicMock()
    fake_bridge.build_tools.return_value = [_fake_tool("zapier_send_email")]

    with patch("argus.tools.settings.zapier_mcp_url", "https://mcp.zapier.com/api/mcp/abc123"), \
         patch("argus.tools.settings.zapier_mcp_api_key", "secret-key"), \
         patch("argus.mcp_bridge.McpServerBridge", return_value=fake_bridge) as bridge_cls:
        registry = build_default_registry()

    assert "zapier_send_email" in registry._tools
    bridge_cls.assert_called_once_with(
        url="https://mcp.zapier.com/api/mcp/abc123",
        headers={"Authorization": "Bearer secret-key"},
    )


def test_zapier_without_an_api_key_sends_no_auth_header():
    fake_bridge = MagicMock()
    fake_bridge.build_tools.return_value = []

    with patch("argus.tools.settings.zapier_mcp_url", "https://mcp.zapier.com/api/mcp/abc123"), \
         patch("argus.tools.settings.zapier_mcp_api_key", ""), \
         patch("argus.mcp_bridge.McpServerBridge", return_value=fake_bridge) as bridge_cls:
        build_default_registry()

    bridge_cls.assert_called_once_with(url="https://mcp.zapier.com/api/mcp/abc123", headers=None)


def test_zapier_startup_failure_does_not_break_the_rest_of_the_registry():
    with patch("argus.tools.settings.zapier_mcp_url", "https://mcp.zapier.com/api/mcp/abc123"), \
         patch("argus.mcp_bridge.McpServerBridge", side_effect=RuntimeError("unreachable")):
        registry = build_default_registry()

    assert "read_file" in registry._tools
    assert not any(name.startswith("zapier_") for name in registry._tools)


def test_home_assistant_tools_absent_without_a_configured_url():
    with patch("argus.tools.settings.home_assistant_mcp_url", ""):
        registry = build_default_registry()

    assert not any(name.startswith("home_assistant_") for name in registry._tools)


def test_home_assistant_tools_registered_when_url_is_set():
    fake_bridge = MagicMock()
    fake_bridge.build_tools.return_value = [_fake_tool("home_assistant_turn_on_light")]

    with patch("argus.tools.settings.home_assistant_mcp_url", "http://homeassistant.local:8123/mcp_server/sse"), \
         patch("argus.tools.settings.home_assistant_mcp_token", "hass-token"), \
         patch("argus.mcp_bridge.McpServerBridge", return_value=fake_bridge) as bridge_cls:
        registry = build_default_registry()

    assert "home_assistant_turn_on_light" in registry._tools
    bridge_cls.assert_called_once_with(
        url="http://homeassistant.local:8123/mcp_server/sse",
        headers={"Authorization": "Bearer hass-token"},
    )


def test_home_assistant_startup_failure_does_not_break_the_rest_of_the_registry():
    with patch("argus.tools.settings.home_assistant_mcp_url", "http://homeassistant.local:8123/mcp_server/sse"), \
         patch("argus.mcp_bridge.McpServerBridge", side_effect=RuntimeError("unreachable")):
        registry = build_default_registry()

    assert "read_file" in registry._tools
    assert not any(name.startswith("home_assistant_") for name in registry._tools)
