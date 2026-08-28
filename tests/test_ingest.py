from unittest.mock import MagicMock

import pytest

from argus.ingest import UnsupportedFileType, _chunk, _extract_text, ingest_file


def test_chunk_empty_text_returns_no_chunks():
    assert _chunk("") == []
    assert _chunk("   ") == []


def test_chunk_short_text_is_one_chunk():
    text = "hello world"
    assert _chunk(text) == [text]


def test_chunk_long_text_splits_with_overlap():
    text = "a" * 4000
    chunks = _chunk(text)
    assert len(chunks) > 1
    # Overlap: the tail of chunk[0] should reappear at the head of chunk[1].
    assert chunks[0][-100:] in chunks[1]


def test_extract_text_txt(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("hello from a text file")
    assert _extract_text(path) == "hello from a text file"


def test_extract_text_md(tmp_path):
    path = tmp_path / "notes.md"
    path.write_text("# Heading\n\nBody text")
    assert _extract_text(path) == "# Heading\n\nBody text"


def test_extract_text_unsupported_extension_raises(tmp_path):
    path = tmp_path / "file.docx"
    path.write_text("irrelevant")
    with pytest.raises(UnsupportedFileType):
        _extract_text(path)


def test_ingest_file_stores_one_chunk_per_short_document(tmp_path):
    path = tmp_path / "note.txt"
    path.write_text("a short note")
    store = MagicMock()

    count = ingest_file(path, store=store)

    assert count == 1
    assert store.add.call_count == 1
    call = store.add.call_args
    assert call.kwargs["text"] == "a short note"
    assert call.kwargs["metadata"]["source"] == "ingest"
    assert call.kwargs["metadata"]["filename"] == "note.txt"


def test_ingest_file_reingesting_same_path_uses_same_doc_ids(tmp_path):
    """Doc ids must be deterministic per path so re-ingesting (e.g. after
    editing a file) upserts over the old chunks instead of duplicating them."""
    path = tmp_path / "note.txt"
    path.write_text("version one")
    store = MagicMock()

    ingest_file(path, store=store)
    first_id = store.add.call_args.kwargs["doc_id"]

    path.write_text("version two, a completely different length of text")
    ingest_file(path, store=store)
    second_id = store.add.call_args.kwargs["doc_id"]

    assert first_id == second_id


def test_ingest_file_empty_document_stores_nothing(tmp_path):
    path = tmp_path / "empty.txt"
    path.write_text("")
    store = MagicMock()

    count = ingest_file(path, store=store)

    assert count == 0
    store.add.assert_not_called()
