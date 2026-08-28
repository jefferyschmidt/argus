from argus.memory.store import get_connection
from argus.tools.research_topics import (
    _list_research_topics,
    _track_research_topic,
    _untrack_research_topic,
)


def test_empty_topic_is_rejected():
    result = _track_research_topic({"topic": "   "})
    assert result.startswith("error:")


def test_track_list_untrack_round_trip(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "argus.tools.research_topics.get_connection", lambda: get_connection(tmp_path / "test.db")
    )

    tracked = _track_research_topic({"topic": "SpaceX Starship launches"})
    assert "SpaceX Starship launches" in tracked

    listed = _list_research_topics({})
    assert "SpaceX Starship launches" in listed
    assert "never checked yet" in listed

    topic_id = int(listed.split("#")[1].split(":")[0])
    cancelled = _untrack_research_topic({"topic_id": topic_id})
    assert str(topic_id) in cancelled

    listed_after = _list_research_topics({})
    assert "disabled" in listed_after


def test_list_with_no_topics(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "argus.tools.research_topics.get_connection", lambda: get_connection(tmp_path / "test.db")
    )
    assert _list_research_topics({}) == "No tracked research topics."
