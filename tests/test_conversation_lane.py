from unittest.mock import MagicMock, patch

from argus.llm.base import CompletionResult, Tier
from argus.orchestrator import Orchestrator
from argus.persona import CONVERSATION_PROMPT


def _orchestrator():
    orch = Orchestrator.__new__(Orchestrator)
    orch.memory = MagicMock()
    orch.memory.dialogue_messages.return_value = []
    orch.memory.build_conversation_context.return_value = ""
    orch.router = MagicMock()
    orch.router.cost_governor.spend_today = 0.0
    orch.router.cost_governor.daily_cap_usd = 5.0
    orch.tools = MagicMock()
    orch.last_expression = None
    orch.last_tier = None
    orch.last_model = None
    return orch


def test_casual_tool_word_mention_stays_in_conversation_lane():
    orch = _orchestrator()
    orch.router.complete.return_value = CompletionResult("Looks solid.", Tier.FAST, "test")

    with patch("argus.orchestrator.ui_events.publish"):
        orch.handle("What do you think about my camera setup?")

    orch.router.complete.assert_called_once()
    orch.router.complete_with_tools.assert_not_called()


def test_explicit_recall_is_the_only_casual_path_that_requests_semantic_context():
    orch = _orchestrator()
    orch.memory.build_conversation_context.return_value = "# Relevant recalled context\n- Likes black coffee"

    system = orch._chat_system("Do you remember how I take coffee?")

    assert system.startswith(CONVERSATION_PROMPT.rstrip())
    orch.memory.build_conversation_context.assert_called_once_with(
        "Do you remember how I take coffee?", include_recall=True
    )


def test_persona_requires_uncertain_personal_context_to_be_asked_not_invented():
    assert "Never invent a\nrelationship" in CONVERSATION_PROMPT
