from argus.memory.core import CoreMemoryStore
from argus.memory.episodic import EpisodicStore
from argus.memory.manager import MemoryManager
from argus.memory.store import get_connection


class _FakeSemanticStore:
    """Stands in for SemanticStore so these tests don't need a real Chroma
    instance -- only the interface export_all()/delete_all() matters here,
    the actual embedding/vector-search behavior is SemanticStore's own
    concern, not MemoryManager's."""

    def __init__(self):
        self.docs = [{"id": "episode-1", "text": "hello", "metadata": {"role": "user"}}]
        self.deleted = False

    def export_all(self):
        return self.docs

    def delete_all(self):
        count = len(self.docs)
        self.docs = []
        self.deleted = True
        return count


def _manager(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    mm = MemoryManager.__new__(MemoryManager)
    mm.session_id = "test-session"
    mm.conn = conn
    mm.core = CoreMemoryStore(conn)
    mm.episodic = EpisodicStore(conn)
    mm.semantic = _FakeSemanticStore()
    return mm


def test_export_all_includes_every_category(tmp_path):
    mm = _manager(tmp_path)
    mm.episodic.add(mm.session_id, "user", "hi there")
    mm.core.propose("a pending fact")
    mm.core.add_confirmed("a confirmed fact")

    data = mm.export_all()
    assert data["core_confirmed"] == ["a confirmed fact"]
    assert len(data["core_pending"]) == 1
    assert data["core_pending"][0]["content"] == "a pending fact"
    assert len(data["episodic"]) == 1
    assert data["episodic"][0]["content"] == "hi there"
    assert data["semantic"] == mm.semantic.docs


def test_forget_everything_except_core_leaves_core_memory_alone(tmp_path):
    mm = _manager(tmp_path)
    mm.episodic.add(mm.session_id, "user", "will be deleted")
    mm.core.add_confirmed("survives the purge")

    result = mm.forget_everything_except_core()

    assert result == {"episodic_deleted": 1, "semantic_deleted": 1}
    assert mm.episodic.all_rows() == []
    assert mm.semantic.deleted is True
    assert mm.core.list_confirmed() == ["survives the purge"]
