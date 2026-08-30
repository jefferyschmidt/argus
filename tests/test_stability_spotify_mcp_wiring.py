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


def test_stability_tools_absent_by_default():
    with patch("argus.tools.settings.enable_stability_mcp", False):
        registry = build_default_registry()

    assert not any(name.startswith("stability_") for name in registry._tools)


def test_stability_tools_registered_when_enabled():
    fake_bridge = MagicMock()
    fake_bridge.build_tools.return_value = [_fake_tool("stability_generate_image")]

    with patch("argus.tools.settings.enable_stability_mcp", True), \
         patch("argus.tools.settings.stability_ai_api_key", "sk-test"), \
         patch("argus.mcp_bridge.McpServerBridge", return_value=fake_bridge) as bridge_cls:
        registry = build_default_registry()

    assert "stability_generate_image" in registry._tools
    args, kwargs = bridge_cls.call_args
    assert args == ("npx", ["-y", "mcp-server-stability-ai"])
    assert kwargs["env"]["STABILITY_AI_API_KEY"] == "sk-test"
    assert "IMAGE_STORAGE_DIRECTORY" in kwargs["env"]


def test_stability_startup_failure_does_not_break_the_rest_of_the_registry():
    """Confirmed live as the real failure mode: no API key configured
    makes the server hard-exit at startup."""
    with patch("argus.tools.settings.enable_stability_mcp", True), \
         patch("argus.mcp_bridge.McpServerBridge", side_effect=RuntimeError("STABILITY_AI_API_KEY is a required environment variable")):
        registry = build_default_registry()

    assert "read_file" in registry._tools
    assert not any(name.startswith("stability_") for name in registry._tools)


def test_spotify_tools_absent_by_default():
    with patch("argus.tools.settings.enable_spotify_mcp", False):
        registry = build_default_registry()

    assert not any(name.startswith("spotify_") for name in registry._tools)


def test_spotify_tools_registered_when_enabled():
    """Confirmed live: the real server connects and lists tools fine with
    zero credentials configured (only setup/auth-status tools until
    authenticated) -- no API key setting needed here at all."""
    fake_bridge = MagicMock()
    fake_bridge.build_tools.return_value = [_fake_tool("spotify_setup_instructions")]

    with patch("argus.tools.settings.enable_spotify_mcp", True), \
         patch("argus.mcp_bridge.McpServerBridge", return_value=fake_bridge) as bridge_cls:
        registry = build_default_registry()

    assert "spotify_setup_instructions" in registry._tools
    bridge_cls.assert_called_once_with("npx", ["-y", "@tbrgeek/spotify-mcp-server"])


def test_spotify_startup_failure_does_not_break_the_rest_of_the_registry():
    with patch("argus.tools.settings.enable_spotify_mcp", True), \
         patch("argus.mcp_bridge.McpServerBridge", side_effect=RuntimeError("npx not found")):
        registry = build_default_registry()

    assert "read_file" in registry._tools
    assert not any(name.startswith("spotify_") for name in registry._tools)
