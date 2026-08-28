from unittest.mock import patch

from argus.tools.ingest import _ingest_document


def test_missing_file_reports_error(tmp_path):
    result = _ingest_document({"path": str(tmp_path / "nope.txt")})
    assert result.startswith("error:")
    assert "does not exist" in result


def test_directory_is_rejected(tmp_path):
    result = _ingest_document({"path": str(tmp_path)})
    assert result.startswith("error:")
    assert "not a file" in result


def test_successful_ingest_reports_chunk_count(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("hello")
    with patch("argus.tools.ingest.ingest_file", return_value=2):
        result = _ingest_document({"path": str(path)})
    assert "notes.txt" in result
    assert "2" in result


def test_empty_document_reports_nothing_stored(tmp_path):
    path = tmp_path / "empty.txt"
    path.write_text("")
    with patch("argus.tools.ingest.ingest_file", return_value=0):
        result = _ingest_document({"path": str(path)})
    assert "nothing stored" in result


def test_unsupported_type_reports_error(tmp_path):
    path = tmp_path / "file.docx"
    path.write_text("irrelevant")
    result = _ingest_document({"path": str(path)})
    assert result.startswith("error:")
