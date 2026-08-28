import time
from concurrent.futures import Future, ThreadPoolExecutor

from argus.memory.manager import MemoryManager


def _bare_manager():
    """MemoryManager.__new__ + hand-set attrs, same pattern as
    test_memory_thread_safety.py -- avoids touching real ChromaDB/SQLite
    just to test the embed-pool bookkeeping."""
    mm = MemoryManager.__new__(MemoryManager)
    mm._embed_pool = ThreadPoolExecutor(max_workers=1)
    mm._pending_embeds = []
    return mm


def test_flush_waits_for_a_pending_embed_to_finish():
    mm = _bare_manager()
    started = time.monotonic()
    mm._pending_embeds.append(mm._embed_pool.submit(time.sleep, 0.2))

    mm.flush_pending_embeds(timeout=3.0)

    assert time.monotonic() - started >= 0.2
    assert mm._pending_embeds == []


def test_flush_does_not_hang_past_its_timeout():
    """Regression guard: a stuck embed must not block a restart forever --
    this is exactly the scenario flush_pending_embeds exists to bound."""
    mm = _bare_manager()
    mm._pending_embeds.append(mm._embed_pool.submit(time.sleep, 5.0))

    started = time.monotonic()
    mm.flush_pending_embeds(timeout=0.1)
    elapsed = time.monotonic() - started

    assert elapsed < 1.0


def test_flush_with_nothing_pending_returns_immediately():
    mm = _bare_manager()
    mm.flush_pending_embeds(timeout=3.0)  # should not raise or block
    assert mm._pending_embeds == []


def test_remember_turn_tracks_and_prunes_completed_futures(tmp_path, monkeypatch):
    from argus.memory.store import get_connection
    from argus.memory.episodic import EpisodicStore

    mm = MemoryManager.__new__(MemoryManager)
    mm.session_id = "test-session"
    mm.conn = get_connection(tmp_path / "test.db")
    mm.episodic = EpisodicStore(mm.conn)
    mm.semantic = type("FakeSemantic", (), {"add": staticmethod(lambda **kw: None)})()
    mm._embed_pool = ThreadPoolExecutor(max_workers=1)
    mm._pending_embeds = []

    mm.remember_turn("user", "hello")
    mm.flush_pending_embeds(timeout=3.0)

    assert mm._pending_embeds == []
