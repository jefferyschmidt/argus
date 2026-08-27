import json
from unittest.mock import MagicMock

import pytest

from argus.agent.audit import AuditLog
from argus.llm.anthropic_client import AnthropicClient
from argus.llm.base import Tier
from argus.tools.base import PermissionTier, Tool
from argus.tools.registry import ToolRegistry


def _fake_response(stop_reason, content):
    resp = MagicMock()
    resp.stop_reason = stop_reason
    resp.content = content
    resp.usage.input_tokens = 10
    resp.usage.output_tokens = 5
    return resp


def _text_block(text):
    b = MagicMock()
    b.type = "text"
    b.text = text
    return b


def _tool_use_block(name, tool_input, id_="tu1"):
    b = MagicMock()
    b.type = "tool_use"
    b.name = name
    b.input = tool_input
    b.id = id_
    return b


def _client_with_noop_tool():
    client = AnthropicClient.__new__(AnthropicClient)
    registry = ToolRegistry()
    registry.register(Tool(
        name="noop", description="d", input_schema={"type": "object", "properties": {}},
        tier=PermissionTier.ALLOW, handler=lambda args: "ok",
    ))
    return client, registry


def test_on_tool_call_invoked_for_each_tool_execution():
    client, registry = _client_with_noop_tool()
    responses = [
        _fake_response("tool_use", [_tool_use_block("noop", {})]),
        _fake_response("end_turn", [_text_block("done")]),
    ]
    fake_messages = MagicMock()
    fake_messages.create.side_effect = responses
    client._client = MagicMock(messages=fake_messages)

    seen = []
    result = client.complete_with_tools(
        "do something", system="sys", tool_registry=registry,
        tier=Tier.FAST, on_tool_call=lambda name, inp, res: seen.append((name, res)),
    )

    assert result.text == "done"
    assert seen == [("noop", "ok")]


def test_max_iterations_cap_stops_infinite_tool_loop():
    client, registry = _client_with_noop_tool()
    always_tool_use = _fake_response("tool_use", [_tool_use_block("noop", {})])
    fake_messages = MagicMock()
    fake_messages.create.return_value = always_tool_use
    client._client = MagicMock(messages=fake_messages)

    result = client.complete_with_tools(
        "loop forever", system="sys", tool_registry=registry, tier=Tier.FAST, max_iterations=3,
    )

    assert fake_messages.create.call_count == 3
    assert "too many tool iterations" in result.text


def test_on_tool_call_exception_propagates_and_aborts_the_loop():
    client, registry = _client_with_noop_tool()
    resp = _fake_response("tool_use", [_tool_use_block("noop", {})])
    fake_messages = MagicMock()
    fake_messages.create.return_value = resp
    client._client = MagicMock(messages=fake_messages)

    def blow_up(name, inp, res):
        raise TimeoutError("budget exceeded")

    with pytest.raises(TimeoutError):
        client.complete_with_tools("goal", system="sys", tool_registry=registry, on_tool_call=blow_up)

    # aborted on the first tool call, never got a second turn
    assert fake_messages.create.call_count == 1


def test_audit_log_writes_valid_jsonl(tmp_path):
    log = AuditLog(tmp_path / "sub" / "audit.jsonl")
    log.record("goal_started", goal="test the thing")
    log.record("tool_call", name="noop", input={}, result="ok")

    lines = log.path.read_text().strip().splitlines()
    assert len(lines) == 2

    first = json.loads(lines[0])
    assert first["type"] == "goal_started"
    assert first["goal"] == "test the thing"
    assert "ts" in first

    second = json.loads(lines[1])
    assert second["type"] == "tool_call"
    assert second["name"] == "noop"
