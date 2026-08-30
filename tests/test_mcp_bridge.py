import asyncio
from unittest.mock import patch

import pytest

from argus.mcp_bridge import McpServerBridge
from argus.tools.base import PermissionTier


class _FakeToolSchema:
    def __init__(self, name, description, input_schema):
        self.name = name
        self.description = description
        self.input_schema = input_schema


class _FakeListToolsResult:
    def __init__(self, tools):
        self.tools = tools


class _FakeContentBlock:
    def __init__(self, text):
        self.text = text


class _FakeCallToolResult:
    def __init__(self, text, is_error=False):
        self.content = [_FakeContentBlock(text)]
        self.isError = is_error


class _FakeSession:
    """Stands in for mcp.ClientSession -- real async methods, so this
    exercises the actual threaded-event-loop bridging code in
    McpServerBridge, not just the wrapping logic around it."""

    def __init__(self, tools=None, call_results=None):
        self._tools = tools or [
            _FakeToolSchema("browser_navigate", "Navigate to a URL", {"type": "object", "properties": {"url": {"type": "string"}}}),
        ]
        self._call_results = call_results or {}
        self.calls = []

    async def list_tools(self):
        return _FakeListToolsResult(self._tools)

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return self._call_results.get(name, _FakeCallToolResult(f"called {name}"))


def _bridge_with_fake_session(session: _FakeSession) -> McpServerBridge:
    """Builds a real McpServerBridge (real background thread, real event
    loop) but skips the actual subprocess/stdio connection -- _connect is
    replaced with one that just installs the fake session, so this still
    genuinely exercises the run_coroutine_threadsafe bridging that the
    live reference-lifetime bug (garbage-collected context managers
    closing the connection early) actually lived in."""

    async def fake_connect(self):
        self._session = session
        self._ready.set()

    with patch.object(McpServerBridge, "_connect", fake_connect):
        return McpServerBridge("fake-command")


def test_build_tools_wraps_each_mcp_tool_as_an_argus_tool():
    session = _FakeSession()
    bridge = _bridge_with_fake_session(session)
    try:
        tools = bridge.build_tools(name_prefix="pw_")
    finally:
        bridge.close()

    assert len(tools) == 1
    tool = tools[0]
    assert tool.name == "pw_browser_navigate"
    assert tool.description == "Navigate to a URL"
    assert tool.tier == PermissionTier.CONFIRM
    assert tool.repeatable is True


def test_handler_dispatches_to_the_correct_underlying_tool_name():
    """Confirmed as a real risk worth a dedicated test: a closure built in
    a loop over multiple tools can accidentally late-bind to the LAST
    tool's name instead of each one's own -- build_tools() guards against
    this with a default-arg capture; this proves it actually works with
    more than one tool."""
    session = _FakeSession(tools=[
        _FakeToolSchema("browser_navigate", "Navigate", {"type": "object", "properties": {}}),
        _FakeToolSchema("browser_click", "Click", {"type": "object", "properties": {}}),
    ])
    bridge = _bridge_with_fake_session(session)
    try:
        tools = {t.name: t for t in bridge.build_tools()}
        tools["browser_navigate"].handler({"url": "https://example.com"})
        tools["browser_click"].handler({"selector": "#go"})
    finally:
        bridge.close()

    assert session.calls == [
        ("browser_navigate", {"url": "https://example.com"}),
        ("browser_click", {"selector": "#go"}),
    ]


def test_call_tool_prefixes_error_results():
    session = _FakeSession(call_results={
        "browser_navigate": _FakeCallToolResult("page not found", is_error=True),
    })
    bridge = _bridge_with_fake_session(session)
    try:
        result = bridge.call_tool("browser_navigate", {"url": "https://bad"})
    finally:
        bridge.close()

    assert result == "error: page not found"


def test_call_tool_returns_plain_text_on_success():
    session = _FakeSession(call_results={
        "browser_navigate": _FakeCallToolResult("navigated ok"),
    })
    bridge = _bridge_with_fake_session(session)
    try:
        result = bridge.call_tool("browser_navigate", {"url": "https://example.com"})
    finally:
        bridge.close()

    assert result == "navigated ok"


def test_missing_input_schema_falls_back_to_an_empty_object_schema():
    """A malformed/empty schema from a third-party server shouldn't produce
    an invalid Anthropic tool schema."""
    session = _FakeSession(tools=[_FakeToolSchema("weird_tool", "does something", None)])
    bridge = _bridge_with_fake_session(session)
    try:
        tool = bridge.build_tools()[0]
    finally:
        bridge.close()

    assert tool.input_schema == {"type": "object", "properties": {}}


def test_init_propagates_a_connect_failure():
    async def failing_connect(self):
        raise RuntimeError("server unreachable")

    with patch.object(McpServerBridge, "_connect", failing_connect):
        with pytest.raises(RuntimeError, match="server unreachable"):
            McpServerBridge("fake-command")


def test_init_times_out_if_the_server_never_becomes_ready():
    async def hanging_connect(self):
        await asyncio.sleep(3600)

    with patch.object(McpServerBridge, "_connect", hanging_connect), \
         patch("argus.mcp_bridge._CONNECT_TIMEOUT_SECONDS", 0.2):
        with pytest.raises(TimeoutError):
            McpServerBridge("fake-command")
