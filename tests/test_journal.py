from argus.memory.journal import JournalStore
from argus.memory.store import get_connection
from argus.tools.journal import _search_journal
from argus.voice.loop import _JOURNAL_TRIGGER


def _store(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    return JournalStore(conn)


def test_add_and_list_recent(tmp_path):
    store = _store(tmp_path)
    store.add("first thought")
    store.add("second thought")

    rows = store.list_recent()
    assert [r["text"] for r in rows] == ["first thought", "second thought"]


def test_search_matches_substring(tmp_path):
    store = _store(tmp_path)
    store.add("thinking about the auth refactor")
    store.add("what to get at the grocery store")

    rows = store.search("refactor")
    assert len(rows) == 1
    assert "auth refactor" in rows[0]["text"]


def test_search_no_match_returns_empty(tmp_path):
    store = _store(tmp_path)
    store.add("something unrelated")
    assert store.search("nonexistent") == []


def test_journal_trigger_matches_inline_content():
    m = _JOURNAL_TRIGGER.match("note to self: call the dentist tomorrow")
    assert m is not None
    assert m.group(2) == "call the dentist tomorrow"


def test_journal_trigger_matches_alone_with_empty_remainder():
    m = _JOURNAL_TRIGGER.match("take a note")
    assert m is not None
    assert m.group(2) == ""


def test_journal_trigger_case_insensitive_and_other_phrasings():
    assert _JOURNAL_TRIGGER.match("Journal this - the deploy went smoothly today")
    assert _JOURNAL_TRIGGER.match("LOG THIS THOUGHT, remember to follow up with Sam")


def test_non_trigger_text_does_not_match():
    assert _JOURNAL_TRIGGER.match("what's the weather like") is None
    assert _JOURNAL_TRIGGER.match("can you take a screenshot") is None


def test_search_journal_tool_reports_empty_state(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "argus.tools.journal.get_connection",
        lambda: get_connection(tmp_path / "test.db"),
    )
    assert _search_journal({}) == "No journal entries yet."
    assert _search_journal({"query": "anything"}) == "No matching journal entries."


def test_search_journal_tool_returns_formatted_entries(tmp_path, monkeypatch):
    conn = get_connection(tmp_path / "test.db")
    JournalStore(conn).add("a real entry")
    conn.close()
    monkeypatch.setattr(
        "argus.tools.journal.get_connection",
        lambda: get_connection(tmp_path / "test.db"),
    )
    result = _search_journal({})
    assert "a real entry" in result
