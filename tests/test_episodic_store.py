from argus.memory.episodic import EpisodicStore
from argus.memory.store import get_connection


def _store(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    return EpisodicStore(conn)


def test_all_rows_spans_every_session(tmp_path):
    store = _store(tmp_path)
    store.add("session-a", "user", "hello from a")
    store.add("session-b", "user", "hello from b")

    rows = store.all_rows()
    assert len(rows) == 2
    assert {r["session_id"] for r in rows} == {"session-a", "session-b"}


def test_delete_all_removes_every_session_and_returns_count(tmp_path):
    store = _store(tmp_path)
    store.add("session-a", "user", "one")
    store.add("session-b", "user", "two")
    store.add("session-b", "assistant", "three")

    deleted = store.delete_all()
    assert deleted == 3
    assert store.all_rows() == []
    assert store.recent("session-a") == []
    assert store.recent("session-b") == []
