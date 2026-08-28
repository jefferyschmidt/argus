from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

from argus.memory.manager import MemoryManager, _is_worth_embedding


def test_short_and_filler_turns_are_not_worth_embedding():
    assert _is_worth_embedding("ok") is False
    assert _is_worth_embedding("Okay!") is False
    assert _is_worth_embedding("thanks") is False
    assert _is_worth_embedding("You're welcome!") is False
    assert _is_worth_embedding("hi") is False
    assert _is_worth_embedding("  ") is False


def test_real_content_is_worth_embedding():
    assert _is_worth_embedding("My dog's name is Biscuit, a golden retriever.") is True
    assert _is_worth_embedding("Remind me to call the dentist tomorrow at 3pm.") is True


def _bare_manager(tmp_path):
    from argus.memory.episodic import EpisodicStore
    from argus.memory.store import get_connection

    mm = MemoryManager.__new__(MemoryManager)
    mm.session_id = "test-session"
    mm.conn = get_connection(tmp_path / "test.db")
    mm.episodic = EpisodicStore(mm.conn)
    mm.semantic = MagicMock()
    mm._embed_pool = ThreadPoolExecutor(max_workers=1)
    mm._pending_embeds = []
    return mm


def test_remember_turn_skips_embedding_a_filler_reply(tmp_path):
    mm = _bare_manager(tmp_path)

    mm.remember_turn("assistant", "You're welcome!")
    mm.flush_pending_embeds(timeout=3.0)

    mm.semantic.add.assert_not_called()
    # Still recorded in episodic (short-term recency), just not embedded.
    assert len(mm.episodic.recent(mm.session_id, limit=10)) == 1


def test_remember_turn_still_embeds_real_content(tmp_path):
    mm = _bare_manager(tmp_path)

    mm.remember_turn("user", "My dog's name is Biscuit, a golden retriever.")
    mm.flush_pending_embeds(timeout=3.0)

    mm.semantic.add.assert_called_once()
