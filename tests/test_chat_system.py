from unittest.mock import MagicMock

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
