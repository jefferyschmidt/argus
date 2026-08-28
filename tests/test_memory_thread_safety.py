import threading

from argus.memory.manager import MemoryManager


def test_memory_connection_usable_from_a_different_thread(tmp_path):
    """Regression test: text input and push-to-talk each run on their own
    thread but share the same MemoryManager/connection as the main voice
    loop. sqlite3 blocks cross-thread use of the same connection object by
    default -- confirmed live as a real crash before check_same_thread=False
    was added to get_connection()."""
    from argus.memory.store import get_connection

    conn = get_connection(tmp_path / "test.db")
    mm = MemoryManager.__new__(MemoryManager)
    mm.session_id = "test-session"
    mm.conn = conn
    from argus.memory.episodic import EpisodicStore
    mm.episodic = EpisodicStore(conn)

    errors = []

    def from_other_thread():
        try:
            mm.episodic.add(mm.session_id, "user", "message from another thread")
        except Exception as e:
            errors.append(e)

    t = threading.Thread(target=from_other_thread)
    t.start()
    t.join()

    assert errors == []
    rows = mm.episodic.recent(mm.session_id, limit=10)
    assert len(rows) == 1
