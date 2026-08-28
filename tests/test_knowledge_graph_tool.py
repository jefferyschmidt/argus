from argus.memory.store import get_connection
from argus.tools.knowledge_graph import _forget_relationship, _query_relationships, _remember_relationship


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


def test_forget_removes_exact_match(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "argus.tools.knowledge_graph.get_connection", lambda: get_connection(tmp_path / "test.db")
    )
    _remember_relationship({"subject": "Jason", "predicate": "works on", "object": "the Coshocton line"})

    result = _forget_relationship({"subject": "Jason", "predicate": "works on", "object": "the Coshocton line"})

    assert "Forgot" in result
    assert "No stored facts" in _query_relationships({"entity": "Jason"})


def test_forget_with_no_match_reports_nothing_to_forget(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "argus.tools.knowledge_graph.get_connection", lambda: get_connection(tmp_path / "test.db")
    )

    result = _forget_relationship({"subject": "Jason", "predicate": "works on", "object": "nonexistent"})

    assert "nothing to forget" in result


def test_forget_is_case_insensitive(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "argus.tools.knowledge_graph.get_connection", lambda: get_connection(tmp_path / "test.db")
    )
    _remember_relationship({"subject": "Jason", "predicate": "works on", "object": "the Coshocton line"})

    result = _forget_relationship({"subject": "JASON", "predicate": "Works On", "object": "THE COSHOCTON LINE"})

    assert "Forgot" in result


def test_query_finds_multiple_people_on_same_project(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "argus.tools.knowledge_graph.get_connection", lambda: get_connection(tmp_path / "test.db")
    )

    _remember_relationship({"subject": "Jason", "predicate": "works on", "object": "the Coshocton line"})
    _remember_relationship({"subject": "Priya", "predicate": "manages", "object": "the Coshocton line"})

    result = _query_relationships({"entity": "Coshocton line"})

    assert "Jason" in result
    assert "Priya" in result
