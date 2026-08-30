from unittest.mock import MagicMock

from argus.llm.anthropic_client import AnthropicClient, _cached_tools, _system_param
from argus.llm.base import Message
from argus.tools.base import PermissionTier, Tool
from argus.tools.registry import ToolRegistry


def test_system_param_without_cacheable_system_is_a_plain_string():
    assert _system_param("dynamic text", "") == "dynamic text"


def test_system_param_with_empty_system_and_no_cacheable_is_not_given():
    import anthropic

    assert _system_param("", "") is anthropic.NOT_GIVEN


def test_system_param_splits_static_and_dynamic_into_two_blocks():
    result = _system_param("dynamic text", "static instructions")

    assert result == [
        {"type": "text", "text": "static instructions", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "dynamic text"},
    ]


def test_system_param_with_cacheable_and_no_dynamic_text_omits_second_block():
    result = _system_param("", "static instructions")

    assert result == [{"type": "text", "text": "static instructions", "cache_control": {"type": "ephemeral"}}]


def test_cached_tools_marks_only_the_last_block():
    registry = ToolRegistry()
    registry.register(Tool(
        name="a", description="d", input_schema={"type": "object", "properties": {}},
        tier=PermissionTier.ALLOW, handler=lambda args: "ok",
    ))
    registry.register(Tool(
        name="b", description="d", input_schema={"type": "object", "properties": {}},
        tier=PermissionTier.ALLOW, handler=lambda args: "ok",
    ))

    tools = _cached_tools(registry)

    assert len(tools) == 3  # a, b, web_search
    assert "cache_control" not in tools[0]
    assert "cache_control" not in tools[1]
    assert tools[-1]["cache_control"] == {"type": "ephemeral"}
    assert tools[-1]["name"] == "web_search"


def test_cached_tools_does_not_mutate_the_shared_web_search_tool_dict():
    """_WEB_SEARCH_TOOL is a shared module-level dict -- _cached_tools must
    copy it before adding cache_control, or every subsequent call (with a
    fresh empty registry, say) would incorrectly carry cache_control too."""
    from argus.llm.anthropic_client import _WEB_SEARCH_TOOL

    registry = ToolRegistry()
    _cached_tools(registry)

    assert "cache_control" not in _WEB_SEARCH_TOOL


def _fake_response(text="done"):
    resp = MagicMock()
    resp.stop_reason = "end_turn"
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp.content = [block]
    resp.usage.input_tokens = 10
    resp.usage.output_tokens = 5
    return resp


def test_complete_with_tools_sends_split_system_when_cacheable_given():
    client = AnthropicClient.__new__(AnthropicClient)
    fake_messages = MagicMock()
    fake_messages.create.return_value = _fake_response()
    client._client = MagicMock(messages=fake_messages)

    client.complete_with_tools(
        "hi", system="dynamic bit", tool_registry=ToolRegistry(), cacheable_system="static bit"
    )

    sent_system = fake_messages.create.call_args.kwargs["system"]
    assert sent_system[0]["cache_control"] == {"type": "ephemeral"}
    assert sent_system[0]["text"] == "static bit"
    assert sent_system[1]["text"] == "dynamic bit"


def test_complete_with_tools_without_cacheable_system_is_unchanged():
    client = AnthropicClient.__new__(AnthropicClient)
    fake_messages = MagicMock()
    fake_messages.create.return_value = _fake_response()
    client._client = MagicMock(messages=fake_messages)

    client.complete_with_tools("hi", system="plain system string", tool_registry=ToolRegistry())

    assert fake_messages.create.call_args.kwargs["system"] == "plain system string"


def test_chat_completion_honors_prompt_cache_and_token_budget():
    client = AnthropicClient.__new__(AnthropicClient)
    fake_messages = MagicMock()
    fake_messages.create.return_value = _fake_response()
    client._client = MagicMock(messages=fake_messages)

    client.complete(
        [Message(role="user", content="hello")],
        system="dynamic bit",
        cacheable_system="static bit",
        max_tokens=400,
    )

    call = fake_messages.create.call_args.kwargs
    assert call["max_tokens"] == 400
    assert call["system"][0]["text"] == "static bit"
    assert call["system"][1]["text"] == "dynamic bit"


def test_tool_completion_receives_prior_conversation_messages():
    client = AnthropicClient.__new__(AnthropicClient)
    fake_messages = MagicMock()
    fake_messages.create.return_value = _fake_response()
    client._client = MagicMock(messages=fake_messages)

    client.complete_with_tools(
        "and now do it",
        system="sys",
        tool_registry=ToolRegistry(),
        prior_messages=[
            Message(role="user", content="open the draft"),
            Message(role="assistant", content="It's open."),
        ],
    )

    assert fake_messages.create.call_args.kwargs["messages"] == [
        {"role": "user", "content": "open the draft"},
        {"role": "assistant", "content": "It's open."},
        {"role": "user", "content": "and now do it"},
    ]


def test_streaming_chat_forwards_text_and_returns_completion():
    client = AnthropicClient.__new__(AnthropicClient)
    stream = MagicMock()
    stream.text_stream = ["Hello", " there."]
    stream.get_final_message.return_value = _fake_response("Hello there.")
    stream_context = MagicMock()
    stream_context.__enter__.return_value = stream
    client._client = MagicMock()
    client._client.messages.stream.return_value = stream_context
    received = []

    result = client.complete_streaming(
        [Message(role="user", content="hi")], system="sys", on_text=received.append
    )

    assert received == ["Hello", " there."]
    assert result.text == "Hello there."
