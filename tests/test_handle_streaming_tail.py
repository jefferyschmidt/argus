from unittest.mock import MagicMock, patch

from argus.llm.base import CompletionResult, Tier
from argus.orchestrator import Orchestrator


def _orchestrator(streamed_deltas, final_text):
    orch = Orchestrator.__new__(Orchestrator)
    orch.memory = MagicMock()
    orch.memory.build_context.return_value = ""
    orch.tools = MagicMock()
    orch.last_tier = None
    orch.last_model = None
    orch.last_expression = None
    orch.router = MagicMock()
    orch.router.cost_governor.spend_today = 0.0
    orch.router.cost_governor.daily_cap_usd = 5.0

    def fake_streaming(user_text, system, tool_registry, on_text, **kwargs):
        for delta in streamed_deltas:
            on_text(delta)
        return CompletionResult(text=final_text, tier=Tier.FAST, model="test")

    orch.router.complete_with_tools_streaming.side_effect = fake_streaming
    return orch


def test_reply_ending_exactly_on_a_sentence_boundary_does_not_crash():
    """SentenceBuffer.flush() returns None (not "") when the reply happened
    to end on a sentence boundary -- a final delta ending in ". " leaves the
    buffer empty. Passing that None straight into the marker stripper raised
    AttributeError and took the whole turn down into the voice loop's
    "Something went wrong on that one" handler."""
    orch = _orchestrator(["All done. "], "All done.")
    spoken = []

    # Deliberately not small talk -- that classifies to the LOCAL tier and
    # takes the non-streaming branch, never reaching the code under test.
    with patch("argus.orchestrator.ui_events.publish"):
        reply = orch.handle_streaming("file that report for me", on_sentence=spoken.append)

    assert reply == "All done."
    assert spoken == ["All done."]


def test_reply_without_trailing_whitespace_still_flushes_its_tail():
    orch = _orchestrator(["Two things. ", "Here's the second"], "Two things. Here's the second")
    spoken = []

    with patch("argus.orchestrator.ui_events.publish"):
        orch.handle_streaming("go", on_sentence=spoken.append)

    assert spoken == ["Two things.", "Here's the second"]
