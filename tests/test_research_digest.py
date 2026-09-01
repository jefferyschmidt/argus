from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from argus.llm.base import CompletionResult, Tier
from argus.memory.research_topics import ResearchTopicStore
from argus.memory.store import get_connection
from argus.research_digest import ResearchDigestWorker


def _worker(reply_text):
    router = MagicMock()
    router.complete_with_tools.return_value = CompletionResult(text=reply_text, tier=Tier.ADVANCED, model="test")
    dispatcher = MagicMock()
    return ResearchDigestWorker(router, dispatcher), router, dispatcher


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test.db"


@pytest.fixture
def conn(db_path):
    c = get_connection(db_path)
    yield c
    c.close()


def _patched_get_connection(db_path):
    # A fresh connection per call (same underlying db file) -- matches
    # production, where get_connection() always opens anew. Reusing one
    # connection object across calls would mean check_now()'s own
    # conn.close() closes it out from under the later _check_topic() call
    # that re-opens "the same" connection.
    return patch("argus.memory.store.get_connection", side_effect=lambda: get_connection(db_path))


def test_none_reply_does_not_submit_and_preserves_no_digest(conn, db_path):
    store = ResearchTopicStore(conn)
    store.add("topic a")
    worker, router, dispatcher = _worker("NONE")

    with _patched_get_connection(db_path):
        worker.check_now()

    dispatcher.submit.assert_not_called()
    row = store.list_all()[0]
    assert row["last_digest"] is None
    assert row["last_checked_at"] is not None


def test_real_digest_is_submitted_and_recorded(conn, db_path):
    store = ResearchTopicStore(conn)
    store.add("topic a")
    worker, router, dispatcher = _worker("There's a new paper out on this that's worth a look.")

    with _patched_get_connection(db_path):
        worker.check_now()

    dispatcher.submit.assert_called_once()
    (candidate,), _kwargs = dispatcher.submit.call_args
    assert "new paper" in candidate.text
    assert candidate.kind == "research.digest"
    assert candidate.subject == "topic a"
    row = store.list_all()[0]
    assert row["last_digest"] == "There's a new paper out on this that's worth a look."


def test_disabled_topics_are_skipped(conn, db_path):
    store = ResearchTopicStore(conn)
    topic_id = store.add("topic a")
    store.cancel(topic_id)
    worker, router, dispatcher = _worker("Something new.")

    with _patched_get_connection(db_path):
        worker.check_now()

    router.complete_with_tools.assert_not_called()
    dispatcher.submit.assert_not_called()


def test_prior_digest_is_included_in_the_next_prompt(conn, db_path):
    store = ResearchTopicStore(conn)
    topic_id = store.add("topic a")
    store.record_check(topic_id, datetime(2026, 1, 1), "earlier digest text")
    worker, router, dispatcher = _worker("NONE")

    with _patched_get_connection(db_path):
        worker.check_now()

    prompt = router.complete_with_tools.call_args[0][0]
    assert "earlier digest text" in prompt


def test_one_topic_failure_does_not_block_others(conn, db_path):
    store = ResearchTopicStore(conn)
    store.add("topic a")
    store.add("topic b")
    router = MagicMock()
    router.complete_with_tools.side_effect = [
        RuntimeError("boom"),
        CompletionResult(text="NONE", tier=Tier.ADVANCED, model="test"),
    ]
    dispatcher = MagicMock()
    worker = ResearchDigestWorker(router, dispatcher)

    with _patched_get_connection(db_path):
        worker.check_now()

    assert router.complete_with_tools.call_count == 2
