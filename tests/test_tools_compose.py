from argus.spine.store import SpineStore
from argus.tools.base import PermissionTier
from argus.tools.compose import _build_compose_document


def _tool(tmp_path, monkeypatch):
    monkeypatch.setattr("argus.compose.compose.settings.argus_data_dir", str(tmp_path))
    spine = SpineStore(tmp_path / "spine.db")
    return _build_compose_document(spine), spine


def test_compose_document_is_confirm_tier(tmp_path, monkeypatch):
    tool, _spine = _tool(tmp_path, monkeypatch)
    assert tool.tier == PermissionTier.CONFIRM


def test_compose_document_writes_a_file_and_reports_the_path(tmp_path, monkeypatch):
    tool, spine = _tool(tmp_path, monkeypatch)

    result = tool.handler({
        "title": "Weekly Summary", "format": "markdown",
        "sections": [{"heading": "Overview", "body": "All quiet."}],
    })

    assert "Document composed:" in result
    assert spine.count(kind="document.composed") == 1


def test_compose_document_defaults_to_markdown(tmp_path, monkeypatch):
    tool, _spine = _tool(tmp_path, monkeypatch)

    result = tool.handler({"title": "x", "sections": [{"body": "y"}]})

    assert ".md" in result


def test_compose_document_section_without_heading(tmp_path, monkeypatch):
    tool, _spine = _tool(tmp_path, monkeypatch)
    result = tool.handler({"title": "x", "sections": [{"body": "just body text"}]})
    assert "Document composed:" in result
