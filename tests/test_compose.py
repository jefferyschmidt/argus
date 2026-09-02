import time
from pathlib import Path
from unittest.mock import patch

import pytest

from argus.compose.compose import DocumentSection, DocumentSpec, UnsupportedFormat, compose
from argus.spine.store import SpineStore


@pytest.fixture(autouse=True)
def _isolated_documents_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("argus.compose.compose.settings.argus_data_dir", str(tmp_path))
    return tmp_path


def _spec(**overrides) -> DocumentSpec:
    defaults = dict(
        title="Weekly Summary", format="markdown",
        sections=[DocumentSection(heading="Overview", body="Nothing much happened.")],
    )
    defaults.update(overrides)
    return DocumentSpec(**defaults)


# -- happy path -----------------------------------------------------------

def test_composed_document_exists_on_disk_at_the_returned_path():
    path = compose(_spec())

    assert path.exists()
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "Weekly Summary" in text
    assert "Nothing much happened." in text


def test_markdown_format_renders_headings():
    path = compose(_spec(format="markdown"))
    text = path.read_text(encoding="utf-8")
    assert "## Overview" in text


def test_html_format_renders_and_escapes_content():
    path = compose(_spec(
        format="html",
        sections=[DocumentSection(heading="<script>bad</script>", body="a & b < c")],
    ))
    text = path.read_text(encoding="utf-8")
    assert path.suffix == ".html"
    assert "<script>bad</script>" not in text  # escaped, not executed
    assert "&lt;script&gt;" in text
    assert "a &amp; b &lt; c" in text


def test_document_lands_under_data_dir_documents():
    path = compose(_spec())
    assert path.parent.name == "documents"


def test_unsupported_format_raises():
    with pytest.raises(UnsupportedFormat):
        compose(_spec(format="pdf"))


def test_different_titles_do_not_collide():
    path_a = compose(_spec(title="Report A"))
    path_b = compose(_spec(title="Report B"))
    assert path_a != path_b
    assert path_a.exists()
    assert path_b.exists()


# -- document.composed emission ---------------------------------------------

def test_emits_exactly_one_document_composed_observation(tmp_path):
    spine = SpineStore(tmp_path / "spine.db")

    path = compose(_spec(), spine=spine)

    observations = spine.query(kinds=["document.composed"])
    assert len(observations) == 1
    assert observations[0].payload["path"] == str(path)
    assert observations[0].subject == "Weekly Summary"


def test_no_spine_no_observation_but_still_composes():
    path = compose(_spec(), spine=None)
    assert path.exists()  # works standalone, e.g. for direct unit testing


def test_two_compositions_emit_two_separate_observations(tmp_path):
    spine = SpineStore(tmp_path / "spine.db")
    compose(_spec(title="First"), spine=spine)
    compose(_spec(title="Second"), spine=spine)

    assert spine.count(kind="document.composed") == 2


# -- atomic write / no partial file -----------------------------------------

def test_a_failure_mid_write_leaves_no_partial_file_at_all():
    """§8 acceptance: a composition that dies partway leaves no partial
    file. Simulates the write itself being interrupted -- the one place
    inside compose() a partial file could physically appear."""
    with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            compose(_spec())

    from argus.config import settings

    documents_dir = settings.data_dir / "documents"
    if documents_dir.exists():
        leftovers = list(documents_dir.iterdir())
        assert leftovers == [], f"stray file(s) left behind: {leftovers}"


def test_a_failure_during_rendering_never_touches_disk():
    """A failure before any file I/O starts (e.g. an unsupported format)
    must not create even a temp file."""
    from argus.config import settings

    documents_dir_before = settings.data_dir / "documents"
    existed_before = documents_dir_before.exists()

    with pytest.raises(UnsupportedFormat):
        compose(_spec(format="pdf"))

    if not existed_before:
        assert not documents_dir_before.exists() or list(documents_dir_before.iterdir()) == []


def test_final_file_only_appears_after_the_full_write_succeeds(tmp_path):
    """The temp-file-then-replace mechanism itself: no file at the final
    path exists while the write is still in flight."""
    documents_dir = tmp_path / "documents"

    real_write_text = Path.write_text

    def _check_no_final_file_yet(self, *args, **kwargs):
        # At the moment content is being written to the temp file, the
        # real target path must not exist yet.
        if self.name.endswith(".tmp"):
            final_candidates = list(documents_dir.glob("*.md")) if documents_dir.exists() else []
            assert final_candidates == []
        return real_write_text(self, *args, **kwargs)

    with patch("pathlib.Path.write_text", _check_no_final_file_yet):
        path = compose(_spec())

    assert path.exists()


# -- spine_query section --------------------------------------------------

def test_spine_query_appends_an_observations_section(tmp_path):
    from argus.spine.observation import Observation

    spine = SpineStore(tmp_path / "spine.db")
    spine.record(Observation(source="test", kind="mail.received", ts=time.time(), subject="a@x.com", dedupe_key="a"))

    path = compose(_spec(spine_query={"kinds": ["mail.received"]}), spine=spine)
    text = path.read_text(encoding="utf-8")
    assert "mail.received" in text
    assert "a@x.com" in text


def test_spine_query_with_no_matches_says_none(tmp_path):
    spine = SpineStore(tmp_path / "spine.db")
    path = compose(_spec(spine_query={"kinds": ["mail.received"]}), spine=spine)
    text = path.read_text(encoding="utf-8")
    assert "(none)" in text


def test_spine_query_ignored_without_a_spine():
    path = compose(_spec(spine_query={"kinds": ["mail.received"]}), spine=None)
    text = path.read_text(encoding="utf-8")
    assert "Observations" not in text
