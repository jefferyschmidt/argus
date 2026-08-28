from unittest.mock import MagicMock

import anthropic
import httpx

from argus.llm.base import CompletionResult, Message, Tier
from argus.llm.router import ModelRouter, _OFFLINE_NO_LOCAL_MESSAGE


def _connection_error():
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.APIConnectionError(request=req)


def _router(local_available: bool):
    router = ModelRouter.__new__(ModelRouter)
    router.local = MagicMock()
    router.local.is_available.return_value = local_available
    router.frontier = MagicMock()
    router.cost_governor = MagicMock()
    return router


def test_complete_falls_back_to_local_when_frontier_unreachable():
    router = _router(local_available=True)
    router.frontier.complete.side_effect = _connection_error()
    router.local.complete.return_value = CompletionResult(
        text="I think it's Tuesday.", tier=Tier.LOCAL, model="llama3.2:3b"
    )

    result = router.complete([Message(role="user", content="what day is it")])

    assert "I think it's Tuesday." in result.text
    assert "offline" in result.text.lower()
    router.cost_governor.record.assert_not_called()


def test_complete_returns_offline_message_when_nothing_is_reachable():
    router = _router(local_available=False)
    router.frontier.complete.side_effect = _connection_error()

    result = router.complete([Message(role="user", content="what day is it")])

    assert result.text == _OFFLINE_NO_LOCAL_MESSAGE
    assert result.model == "offline"


def test_complete_with_tools_falls_back_without_raising():
    router = _router(local_available=True)
    router.frontier.complete_with_tools.side_effect = _connection_error()
    router.local.complete.return_value = CompletionResult(
        text="Can't search the web right now, but here's what I know.",
        tier=Tier.LOCAL,
        model="llama3.2:3b",
    )

    result = router.complete_with_tools("what's the weather", system="", tool_registry=MagicMock())

    assert "here's what I know" in result.text
    assert "tools/web access" in result.text.lower()


def test_complete_with_tools_streaming_forwards_fallback_text_via_on_text():
    router = _router(local_available=True)
    router.frontier.complete_with_tools_streaming.side_effect = _connection_error()
    router.local.complete.return_value = CompletionResult(
        text="fallback reply", tier=Tier.LOCAL, model="llama3.2:3b"
    )
    on_text = MagicMock()

    result = router.complete_with_tools_streaming(
        "search for something", system="", tool_registry=MagicMock(), on_text=on_text
    )

    on_text.assert_called_once()
    (sent_text,) = on_text.call_args[0]
    assert "fallback reply" in sent_text
    assert sent_text == result.text


def test_offline_fallback_when_local_also_raises():
    router = _router(local_available=True)
    router.frontier.complete.side_effect = _connection_error()
    router.local.complete.side_effect = RuntimeError("ollama also down")

    result = router.complete([Message(role="user", content="hello")])

    assert result.text == _OFFLINE_NO_LOCAL_MESSAGE
