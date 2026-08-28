from unittest.mock import MagicMock, patch

import groq

from argus.llm.base import CompletionResult, Message, Tier
from argus.llm.router import ModelRouter


def _bare_router():
    router = ModelRouter.__new__(ModelRouter)
    router.local = MagicMock()
    router.frontier = MagicMock()
    router.cost_governor = MagicMock()
    router.offline_fallback = MagicMock()
    return router


def _rate_limit_error():
    return groq.RateLimitError("rate limited", response=MagicMock(), body=None)


def test_rate_limited_local_call_retries_once_before_escalating():
    """Confirmed live: Groq's free tier is a tight shared budget (8000 TPM)
    across every LOCAL-tier caller, and its own error usually says "try
    again in ~360ms" -- escalating straight to the paid frontier tier on
    the very first rate-limit hit wastes real money for what's usually a
    short-lived contention spike."""
    router = _bare_router()
    router.local.is_available.return_value = True
    success = CompletionResult(text="hi", tier=Tier.LOCAL, model="test")
    router.local.complete.side_effect = [_rate_limit_error(), success]

    with patch("argus.llm.router.time.sleep") as mock_sleep:
        result = router.complete([Message(role="user", content="hi")], force_tier=Tier.LOCAL)

    assert result is success
    assert router.local.complete.call_count == 2
    mock_sleep.assert_called_once_with(1.5)
    router.frontier.complete.assert_not_called()


def test_still_rate_limited_after_retry_escalates_to_frontier():
    router = _bare_router()
    router.local.is_available.return_value = True
    router.local.complete.side_effect = [_rate_limit_error(), _rate_limit_error()]
    router.cost_governor.check.return_value = None
    escalated = CompletionResult(text="hi", tier=Tier.FAST, model="test", input_tokens=1, output_tokens=1)
    router.frontier.complete.return_value = escalated

    with patch("argus.llm.router.time.sleep"), \
         patch("argus.llm.router.estimate_cost", return_value=0.001):
        result = router.complete([Message(role="user", content="hi")], force_tier=Tier.LOCAL)

    assert result is escalated
    assert router.local.complete.call_count == 2
    router.frontier.complete.assert_called_once()


def test_non_rate_limit_exception_escalates_immediately_without_retry():
    router = _bare_router()
    router.local.is_available.return_value = True
    router.local.complete.side_effect = RuntimeError("model crashed")
    router.cost_governor.check.return_value = None
    escalated = CompletionResult(text="hi", tier=Tier.FAST, model="test", input_tokens=1, output_tokens=1)
    router.frontier.complete.return_value = escalated

    with patch("argus.llm.router.time.sleep") as mock_sleep, \
         patch("argus.llm.router.estimate_cost", return_value=0.001):
        result = router.complete([Message(role="user", content="hi")], force_tier=Tier.LOCAL)

    assert result is escalated
    assert router.local.complete.call_count == 1  # no retry for a non-rate-limit error
    mock_sleep.assert_not_called()
