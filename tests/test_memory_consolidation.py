from unittest.mock import MagicMock, patch

from argus.llm.base import CompletionResult, Tier
from argus.memory.consolidation import consolidate, get_cursor, set_cursor
from argus.memory.core import CoreMemoryStore
from argus.memory.episodic import EpisodicStore
from argus.memory.manager import MemoryManager
from argus.memory.store import get_connection


def _router(reply_text):
    router = MagicMock()
    router.complete.return_value = CompletionResult(text=reply_text, tier=Tier.LOCAL, model="test")
    return router


def _memory_manager(tmp_path):
    mm = MemoryManager.__new__(MemoryManager)
    mm.session_id = "test-session"
    mm.conn = get_connection(tmp_path / "test.db")
    mm.core = CoreMemoryStore(mm.conn)
    mm.episodic = EpisodicStore(mm.conn)
    return mm


def test_cursor_defaults_to_zero_and_can_be_advanced(tmp_path):
    conn = get_connection(tmp_path / "cursor.db")
    assert get_cursor(conn) == 0
    set_cursor(conn, 42)
    assert get_cursor(conn) == 42


def test_no_new_episodes_does_nothing(tmp_path):
    mm = _memory_manager(tmp_path)
    router = _router("NONE")

    facts = consolidate(router, mm)

    assert facts == []
    router.complete.assert_not_called()


def test_short_transcript_is_skipped_without_a_model_call(tmp_path):
    mm = _memory_manager(tmp_path)
    mm.episodic.add(mm.session_id, "user", "hi")
    mm.episodic.add(mm.session_id, "assistant", "hey")
    router = _router("NONE")

    facts = consolidate(router, mm)

    assert facts == []
    router.complete.assert_not_called()
    # Cursor still advances so these short turns are never reconsidered.
    assert get_cursor(mm.conn) > 0


def test_none_reply_proposes_nothing(tmp_path):
    mm = _memory_manager(tmp_path)
    for i in range(10):
        mm.episodic.add(mm.session_id, "user", f"can you check the weather for me today, part {i}?")
    router = _router("NONE")

    facts = consolidate(router, mm)

    assert facts == []
    assert mm.core.list_confirmed() == []


def test_durable_facts_are_proposed_not_auto_confirmed(tmp_path):
    mm = _memory_manager(tmp_path)
    for i in range(10):
        mm.episodic.add(mm.session_id, "user", f"my dog Biscuit needs a walk, message {i}")
    router = _router("Has a dog named Biscuit.\nPrefers walking the dog in the morning.")

    facts = consolidate(router, mm)

    assert facts == ["Has a dog named Biscuit.", "Prefers walking the dog in the morning."]
    # Agent-proposed facts start unconfirmed -- must go through review, same
    # as any other agent-proposed core memory.
    assert mm.core.list_confirmed() == []
    pending = mm.core.list_pending()
    assert len(pending) == 2


def test_cursor_advances_so_the_same_episodes_are_never_reprocessed(tmp_path):
    mm = _memory_manager(tmp_path)
    for i in range(10):
        mm.episodic.add(mm.session_id, "user", f"my dog Biscuit needs a walk, message {i}")
    router = _router("Has a dog named Biscuit.")

    consolidate(router, mm)
    cursor_after_first = get_cursor(mm.conn)
    facts_second_run = consolidate(router, mm)

    assert cursor_after_first > 0
    assert facts_second_run == []
    assert router.complete.call_count == 1


def test_proposed_fact_publishes_the_same_review_event_as_a_normal_proposal(tmp_path):
    """Must reuse orchestrator._propose_core_memory's exact event shape so
    a consolidation-distilled fact shows up in the console's existing
    confirm/reject review UI, not a second, separate surface."""
    mm = _memory_manager(tmp_path)
    for i in range(10):
        mm.episodic.add(mm.session_id, "user", f"my dog Biscuit needs a walk, message {i}")
    router = _router("Has a dog named Biscuit.")

    with patch("argus.memory.consolidation.ui_events.publish") as mock_publish:
        consolidate(router, mm)

    mock_publish.assert_called_once()
    event = mock_publish.call_args[0][0]
    assert event["type"] == "core_memory_pending"
    assert event["text"] == "Has a dog named Biscuit."
    assert isinstance(event["id"], int)


def test_failed_llm_call_does_not_advance_the_cursor(tmp_path):
    mm = _memory_manager(tmp_path)
    for i in range(10):
        mm.episodic.add(mm.session_id, "user", f"my dog Biscuit needs a walk, message {i}")
    router = MagicMock()
    router.complete.side_effect = RuntimeError("model unavailable")

    facts = consolidate(router, mm)

    assert facts == []
    assert get_cursor(mm.conn) == 0  # retry next poll, not skipped forever
