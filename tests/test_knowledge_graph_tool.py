from argus.memory.store import get_connection
from argus.tools.knowledge_graph import _query_relationships, _remember_relationship


def test_missing_field_is_rejected():
    result = _remember_relationship({"subject": "Jason", "predicate": "works on", "object": "   "})
    assert result.startswith("error:")


def test_missing_entity_is_rejected():
    result = _query_relationships({"entity": ""})
    assert result.startswith("error:")


def test_remember_and_query_round_trip(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "argus.tools.knowledge_graph.get_connection", lambda: get_connection(tmp_path / "test.db")
    )

    noted = _remember_relationship({"subject": "Jason", "predicate": "works on", "object": "the Coshocton line"})
    assert "Jason" in noted
    assert "Coshocton" in noted

    result = _query_relationships({"entity": "Coshocton"})
    assert "Jason" in result
    assert "works on" in result


def test_query_with_no_matches(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "argus.tools.knowledge_graph.get_connection", lambda: get_connection(tmp_path / "test.db")
    )

    result = _query_relationships({"entity": "nobody"})

    assert "No stored facts" in result


def test_query_finds_multiple_people_on_same_project(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "argus.tools.knowledge_graph.get_connection", lambda: get_connection(tmp_path / "test.db")
    )

    _remember_relationship({"subject": "Jason", "predicate": "works on", "object": "the Coshocton line"})
    _remember_relationship({"subject": "Priya", "predicate": "manages", "object": "the Coshocton line"})

    result = _query_relationships({"entity": "Coshocton line"})

    assert "Jason" in result
    assert "Priya" in result
