from unittest.mock import MagicMock, patch

import pytest

from argus.orchestrator import (
    Orchestrator,
    _chat_max_tokens,
    _is_durable_core_memory_candidate,
    _should_use_tools,
)
from argus.persona import CONVERSATION_PROMPT, PERSONALITY_PROMPT, SYSTEM_PROMPT


def test_chat_system_includes_persona_and_only_requested_recall_context():
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.memory = MagicMock()
    orchestrator.memory.build_conversation_context.return_value = "# Standing user facts\n- Prefers short replies"

    system = orchestrator._chat_system("hey")

    assert system.startswith(PERSONALITY_PROMPT.rstrip())
    assert system.endswith("- Prefers short replies")
    orchestrator.memory.build_conversation_context.assert_called_once_with("hey", include_recall=False)


def test_conversation_prompt_excludes_tool_manual():
    assert "## Tool use" not in CONVERSATION_PROMPT
    assert "## Tool use" in SYSTEM_PROMPT


def test_relative_time_is_not_a_durable_core_memory_candidate():
    assert not _is_durable_core_memory_candidate("Has a dentist appointment tomorrow morning.")
    assert not _is_durable_core_memory_candidate("Had an appointment this morning.")
    assert _is_durable_core_memory_candidate("Prefers morning appointments.")


def test_chat_budget_expands_only_for_substantial_requests():
    assert _chat_max_tokens("hey") < _chat_max_tokens("Explain how memory retrieval works")


def test_tool_lane_requires_an_action_not_a_tool_word_mention():
    assert not _should_use_tools("What do you think about my camera setup?")
    assert _should_use_tools("Take a camera photo")
    assert _should_use_tools("What's the weather?")
    assert _should_use_tools("Remind me to call Mom at five")
    assert _should_use_tools("Open Chrome")


# PRD.md §17 unit 35: the exact baseline table from the spec. Before the
# fix, the five "must flip to True" rows were all False -- a state/
# possession question about a tool subject ("do I have any new mail")
# has no action verb, so it never reached _TOOL_ACTION at all and fell
# through to the chat lane, where Argus denied a capability it actually
# has. Encoded as one parametrized table (not five separate asserts) so
# a future regex tweak can't silently regress one row without a named,
# individually-reported test failure.
@pytest.mark.parametrize("text,expected", [
    # Must flip to True (all were False before unit 35).
    ("do I have any important emails that I haven't seen yet", True),
    ("any new messages", True),
    ("is there anything in my inbox", True),
    ("what's on my calendar today", True),
    ("do I have any reminders set", True),
    # Must stay exactly as they already were.
    ("when is my next meeting", True),
    ("read me my latest email", True),
    ("check my email", True),
    ("what time is it", True),
    # The guardrails: an opinion/sentiment turn must never become a tool
    # lane turn just because a tool-domain word is present -- this is
    # what stops the fix from degenerating into subject-only routing
    # (tried and rejected).
    ("what do you think about my camera", False),
    ("I love the file you made me", False),
    ("tell me a joke", False),
    ("how are you feeling today", False),
])
def test_should_use_tools_baseline_table(text, expected):
    assert _should_use_tools(text) is expected


def _orchestrator_for_routing():
    from argus.llm.base import CompletionResult, Tier

    orch = Orchestrator.__new__(Orchestrator)
    orch.memory = MagicMock()
    orch.memory.build_context.return_value = ""
    orch.memory.build_conversation_context.return_value = ""
    orch.tools = MagicMock()
    orch.last_tier = None
    orch.last_model = None
    orch.last_expression = None
    orch.router = MagicMock()
    orch.router.cost_governor.spend_today = 0.0
    orch.router.cost_governor.daily_cap_usd = 5.0
    orch.router.complete.return_value = CompletionResult(text="chat reply", tier=Tier.FAST, model="test")
    orch.router.complete_with_tools.return_value = CompletionResult(text="tool reply", tier=Tier.ADVANCED, model="test")
    return orch


def test_state_question_reaches_complete_with_tools_not_the_chat_lane():
    """PRD §17 unit 35 acceptance: "do I have any important emails"
    reaches complete_with_tools (the tool path), not complete (the chat
    lane). This is the actual live bug -- confirmed 2026-09-03, Argus
    answered on the fast tier that it had no email access while the
    email watcher was running, because this turn never reached a tool
    at all."""
    orch = _orchestrator_for_routing()

    with patch("argus.orchestrator.ui_events.publish"):
        orch.handle("do I have any important emails that I haven't seen yet")

    orch.router.complete_with_tools.assert_called_once()
    orch.router.complete.assert_not_called()


def test_state_question_reaches_complete_with_tools_streaming_not_the_chat_lane():
    from argus.llm.base import CompletionResult, Tier

    orch = _orchestrator_for_routing()
    orch.router.complete_with_tools_streaming.side_effect = lambda *a, **k: CompletionResult(text="", tier=Tier.ADVANCED, model="test")

    with patch("argus.orchestrator.ui_events.publish"):
        orch.handle_streaming("do I have any important emails that I haven't seen yet", on_sentence=lambda s: None)

    orch.router.complete_with_tools_streaming.assert_called_once()
    orch.router.complete_streaming.assert_not_called()


def test_plain_small_talk_still_uses_the_chat_lane_not_tools():
    """The other half of §17's acceptance: the chat lane's tone/latency
    for plain small talk is unchanged -- "tell me a joke" and "how are
    you" must still skip the tool schema entirely."""
    for text in ("tell me a joke", "how are you feeling today"):
        orch = _orchestrator_for_routing()
        with patch("argus.orchestrator.ui_events.publish"):
            orch.handle(text)
        orch.router.complete.assert_called_once()
        orch.router.complete_with_tools.assert_not_called()


def test_opinion_frame_exclusion_beats_a_state_question_frame():
    """The opinion-frame exclusion is the actual guardrail mechanism
    (PRD §17: "exclude an explicit opinion frame even when a subject is
    present, rather than trying to enumerate the state frames
    exhaustively") -- proven here on a phrase that WOULD otherwise match
    the new state-question route (state frame "is there" + subject
    "inbox"), to show the exclusion is load-bearing and not just
    incidentally unneeded for the baseline table above."""
    text = "what do you think, is there anything important in my inbox"
    assert not _should_use_tools(text)
