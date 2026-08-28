from dataclasses import dataclass
from pathlib import Path

import pytest

from argus import undo_log


@dataclass
class _FakeSettings:
    data_dir: Path


@pytest.fixture
def fake_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(undo_log, "settings", _FakeSettings(data_dir=tmp_path))
    return tmp_path


def test_snapshot_of_new_file_records_did_not_exist(fake_data_dir):
    target = fake_data_dir / "new_file.txt"
    undo_log.snapshot_before_write(target)

    entries = undo_log.list_recent_writes()
    assert len(entries) == 1
    assert entries[0]["existed"] is False
    assert "backup" not in entries[0]


def test_snapshot_of_existing_file_backs_up_content(fake_data_dir):
    target = fake_data_dir / "existing.txt"
    target.write_text("original content")

    undo_log.snapshot_before_write(target)
    target.write_text("new content")  # simulate the actual write happening after the snapshot

    entries = undo_log.list_recent_writes()
    assert entries[0]["existed"] is True
    assert Path(entries[0]["backup"]).read_text() == "original content"


def test_undo_restores_previous_content(fake_data_dir):
    target = fake_data_dir / "doc.txt"
    target.write_text("v1")
    undo_log.snapshot_before_write(target)
    target.write_text("v2 -- oops")

    result = undo_log.undo_write(str(target))

    assert target.read_text() == "v1"
    assert "Restored" in result


def test_undo_removes_file_that_did_not_exist_before(fake_data_dir):
    target = fake_data_dir / "accidental.txt"
    undo_log.snapshot_before_write(target)
    target.write_text("shouldn't have written this")

    result = undo_log.undo_write(str(target))

    assert not target.exists()
    assert "Removed" in result


def test_undo_with_no_path_uses_most_recent_write(fake_data_dir):
    first = fake_data_dir / "first.txt"
    first.write_text("first-v1")
    undo_log.snapshot_before_write(first)
    first.write_text("first-v2")

    second = fake_data_dir / "second.txt"
    second.write_text("second-v1")
    undo_log.snapshot_before_write(second)
    second.write_text("second-v2")

    undo_log.undo_write()  # no path -- should undo the most recent (second)

    assert second.read_text() == "second-v1"
    assert first.read_text() == "first-v2"  # untouched


def test_undo_with_no_tracked_writes_reports_that(fake_data_dir):
    assert undo_log.undo_write() == "No tracked writes to undo."


def test_undo_with_unknown_path_reports_that(fake_data_dir):
    result = undo_log.undo_write(str(fake_data_dir / "never_written.txt"))
    assert "No tracked write found" in result
