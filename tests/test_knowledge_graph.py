import pytest

from argus.memory.knowledge_graph import KnowledgeGraphStore
from argus.memory.store import get_connection


@pytest.fixture
def conn(tmp_path):
    c = get_connection(tmp_path / "test.db")
    yield c
    c.close()


def test_add_and_query_by_subject(conn):
    store = KnowledgeGraphStore(conn)
    store.add("Jason", "works on", "the Coshocton line")

    rows = store.query("Jason")

    assert len(rows) == 1
    assert rows[0]["subject"] == "Jason"
    assert rows[0]["predicate"] == "works on"
    assert rows[0]["object"] == "the Coshocton line"


def test_query_matches_object_side_too(conn):
    store = KnowledgeGraphStore(conn)
    store.add("Jason", "works on", "the Coshocton line")
    store.add("Priya", "manages", "the Coshocton line")

    rows = store.query("Coshocton")

    assert len(rows) == 2
    subjects = {r["subject"] for r in rows}
    assert subjects == {"Jason", "Priya"}


def test_query_case_insensitive_and_substring(conn):
    store = KnowledgeGraphStore(conn)
    store.add("Jason", "works on", "the Coshocton Line")

    rows = store.query("coshocton")

    assert len(rows) == 1


def test_query_with_no_matches_returns_empty(conn):
    store = KnowledgeGraphStore(conn)
    store.add("Jason", "works on", "the Coshocton line")

    rows = store.query("nonexistent entity")

    assert rows == []


def test_duplicate_fact_is_not_duplicated(conn):
    store = KnowledgeGraphStore(conn)
    store.add("Jason", "works on", "the Coshocton line")
    store.add("Jason", "works on", "the Coshocton line")

    rows = store.list_all()

    assert len(rows) == 1


def test_delete_removes_fact(conn):
    store = KnowledgeGraphStore(conn)
    fact_id = store.add("Jason", "works on", "the Coshocton line")

    store.delete(fact_id)

    assert store.list_all() == []


def test_whitespace_is_trimmed_on_add(conn):
    store = KnowledgeGraphStore(conn)
    store.add("  Jason  ", " works on ", " the Coshocton line ")

    row = store.list_all()[0]
    assert row["subject"] == "Jason"
    assert row["predicate"] == "works on"
    assert row["object"] == "the Coshocton line"
