import base64
from unittest.mock import MagicMock

from argus.llm.anthropic_client import AnthropicClient
from argus.llm.base import Tier


def _fake_response(text):
    resp = MagicMock()
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp.content = [block]
    resp.usage.input_tokens = 200
    resp.usage.output_tokens = 40
    return resp


def _client():
    return AnthropicClient.__new__(AnthropicClient)


def test_sends_image_and_prompt_as_a_single_user_message():
    client = _client()
    fake_messages = MagicMock()
    fake_messages.create.return_value = _fake_response("a receipt from a grocery store")
    client._client = MagicMock(messages=fake_messages)

    result = client.complete_with_image(b"raw-jpeg-bytes", "what is this?", tier=Tier.FAST)

    assert result.text == "a receipt from a grocery store"
    call_kwargs = fake_messages.create.call_args.kwargs
    messages = call_kwargs["messages"]
    assert len(messages) == 1
    content = messages[0]["content"]
    assert content[0]["type"] == "image"
    assert content[0]["source"]["data"] == base64.b64encode(b"raw-jpeg-bytes").decode("ascii")
    assert content[1] == {"type": "text", "text": "what is this?"}


def test_no_tools_are_offered_for_a_one_shot_vision_call():
    client = _client()
    fake_messages = MagicMock()
    fake_messages.create.return_value = _fake_response("something")
    client._client = MagicMock(messages=fake_messages)

    client.complete_with_image(b"bytes", "prompt")

    assert "tools" not in fake_messages.create.call_args.kwargs
