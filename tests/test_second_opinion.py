from unittest.mock import MagicMock

from argus.llm.base import CompletionResult, Tier
from argus.tools.second_opinion import _build_second_opinion


def _router(reply_texts):
    router = MagicMock()
    router.complete.side_effect = [
        CompletionResult(text=t, tier=Tier.ADVANCED, model="test") for t in reply_texts
    ]
    return router


def test_missing_question_returns_error_without_calling_router():
    router = _router([])
    tool = _build_second_opinion(router)

    result = tool.handler({})

    assert result.startswith("error:")
    router.complete.assert_not_called()


def test_makes_three_angle_calls_plus_one_synthesis_call():
    router = _router(["skeptic take", "expert take", "risk take", "final synthesis"])
    tool = _build_second_opinion(router)

    result = tool.handler({"question": "should I quit my job to go full-time on a startup"})

    assert router.complete.call_count == 4
    assert result == "final synthesis"


def test_all_calls_use_advanced_tier():
    router = _router(["a", "b", "c", "d"])
    tool = _build_second_opinion(router)

    tool.handler({"question": "big decision"})

    for call in router.complete.call_args_list:
        assert call.kwargs["force_tier"] == Tier.ADVANCED


def test_synthesis_prompt_includes_all_three_angle_takes():
    router = _router(["skeptic take", "expert take", "risk take", "final"])
    tool = _build_second_opinion(router)

    tool.handler({"question": "big decision"})

    synthesis_prompt = router.complete.call_args_list[-1].args[0][0].content
    assert "skeptic take" in synthesis_prompt
    assert "expert take" in synthesis_prompt
    assert "risk take" in synthesis_prompt


def test_angle_failure_returns_error_and_stops_early():
    router = MagicMock()
    router.complete.side_effect = RuntimeError("boom")
    tool = _build_second_opinion(router)

    result = tool.handler({"question": "big decision"})

    assert result.startswith("error:")
    assert router.complete.call_count == 1


def test_synthesis_failure_returns_error():
    router = MagicMock()
    router.complete.side_effect = [
        CompletionResult(text="a", tier=Tier.ADVANCED, model="test"),
        CompletionResult(text="b", tier=Tier.ADVANCED, model="test"),
        CompletionResult(text="c", tier=Tier.ADVANCED, model="test"),
        RuntimeError("synthesis boom"),
    ]
    tool = _build_second_opinion(router)

    result = tool.handler({"question": "big decision"})

    assert result.startswith("error:")
    assert "synthesiz" in result
