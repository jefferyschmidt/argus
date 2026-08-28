from unittest.mock import patch

from argus.memory.core import CoreMemoryStore
from argus.memory.store import get_connection
from argus.tools.memory_review import (
    _confirm_core_memory,
    _list_pending_core_memories,
    _reject_core_memory,
)


def _patched_connection(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    return patch("argus.tools.memory_review.get_connection", return_value=conn), conn


def test_list_pending_reports_none_when_empty(tmp_path):
    patcher, conn = _patched_connection(tmp_path)
    with patcher:
        result = _list_pending_core_memories({})
    assert "No pending" in result


def test_list_pending_shows_id_and_content(tmp_path):
    patcher, conn = _patched_connection(tmp_path)
    CoreMemoryStore(conn).propose("Has a dog named Biscuit.")
    with patcher:
        result = _list_pending_core_memories({})
    assert "Has a dog named Biscuit." in result
    assert "[1]" in result


def test_confirm_moves_a_pending_memory_to_confirmed(tmp_path):
    patcher, conn = _patched_connection(tmp_path)
    store = CoreMemoryStore(conn)
    memory_id = store.propose("Prefers concise replies.")

    with patcher:
        result = _confirm_core_memory({"memory_id": memory_id})

    assert "Confirmed" in result
    assert "Prefers concise replies." in result
    # The tool closes its own connection (same pattern as
    # ui/server.py's _resolve_core_memory) -- verify via a fresh one.
    fresh = CoreMemoryStore(get_connection(tmp_path / "test.db"))
    assert "Prefers concise replies." in fresh.list_confirmed()


def test_reject_deletes_a_pending_memory(tmp_path):
    patcher, conn = _patched_connection(tmp_path)
    store = CoreMemoryStore(conn)
    memory_id = store.propose("Some incorrect guess.")

    with patcher:
        result = _reject_core_memory({"memory_id": memory_id})

    assert "Rejected" in result
    fresh = CoreMemoryStore(get_connection(tmp_path / "test.db"))
    assert fresh.list_pending() == []
    assert fresh.list_confirmed() == []


def test_confirm_unknown_id_reports_an_error(tmp_path):
    patcher, conn = _patched_connection(tmp_path)
    with patcher:
        result = _confirm_core_memory({"memory_id": 999})
    assert result.startswith("error:")


def test_confirm_rejects_non_integer_id(tmp_path):
    patcher, conn = _patched_connection(tmp_path)
    with patcher:
        result = _confirm_core_memory({"memory_id": "not a number"})
    assert result.startswith("error:")


def test_confirm_publishes_the_same_events_the_console_buttons_do(tmp_path):
    patcher, conn = _patched_connection(tmp_path)
    store = CoreMemoryStore(conn)
    memory_id = store.propose("Prefers concise replies.")

    with patcher, patch("argus.tools.memory_review.ui_events.publish") as mock_publish:
        _confirm_core_memory({"memory_id": memory_id})

    published = [call.args[0] for call in mock_publish.call_args_list]
    assert {"type": "core_memory_resolved", "id": memory_id, "confirmed": True} in published
    assert any(e.get("type") == "memory" for e in published)
