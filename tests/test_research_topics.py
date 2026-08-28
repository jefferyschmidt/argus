import pytest

from argus.memory.research_topics import ResearchTopicStore
from argus.memory.store import get_connection


@pytest.fixture
def conn(tmp_path):
    c = get_connection(tmp_path / "test.db")
    yield c
    c.close()


def test_add_and_list(conn):
    store = ResearchTopicStore(conn)
    topic_id = store.add("SpaceX Starship launches")

    rows = store.list_all()

    assert len(rows) == 1
    assert rows[0]["id"] == topic_id
    assert rows[0]["topic"] == "SpaceX Starship launches"
    assert rows[0]["enabled"] == 1
    assert rows[0]["last_checked_at"] is None
    assert rows[0]["last_digest"] is None


def test_list_enabled_excludes_cancelled(conn):
    store = ResearchTopicStore(conn)
    active_id = store.add("topic a")
    cancelled_id = store.add("topic b")
    store.cancel(cancelled_id)

    rows = store.list_enabled()

    assert [r["id"] for r in rows] == [active_id]


def test_record_check_with_digest_updates_last_digest(conn):
    from datetime import datetime

    store = ResearchTopicStore(conn)
    topic_id = store.add("topic a")

    store.record_check(topic_id, datetime(2026, 1, 1, 9, 0), "something happened")

    row = store.list_all()[0]
    assert row["last_digest"] == "something happened"
    assert row["last_checked_at"] is not None


def test_record_check_with_none_digest_preserves_prior_digest(conn):
    from datetime import datetime

    store = ResearchTopicStore(conn)
    topic_id = store.add("topic a")
    store.record_check(topic_id, datetime(2026, 1, 1, 9, 0), "first digest")

    store.record_check(topic_id, datetime(2026, 1, 2, 9, 0), None)

    row = store.list_all()[0]
    assert row["last_digest"] == "first digest"


def test_cancel_disables_topic(conn):
    store = ResearchTopicStore(conn)
    topic_id = store.add("topic a")

    store.cancel(topic_id)

    row = store.list_all()[0]
    assert row["enabled"] == 0
