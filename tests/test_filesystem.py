from dataclasses import dataclass
from pathlib import Path

import pytest

from argus import undo_log
from argus.tools import filesystem


@dataclass
class _FakeSettings:
    workspace_dir: Path


@dataclass
class _FakeUndoSettings:
    data_dir: Path


@pytest.fixture
def roots(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(filesystem, "settings", _FakeSettings(workspace_dir=workspace))
    # _write_file calls undo_log.snapshot_before_write, which reads its own
    # settings reference independently -- without this, tests here were
    # writing real entries into the live data/undo/log.jsonl (confirmed
    # live: found actual pytest tmp_path entries in the real log).
    monkeypatch.setattr(undo_log, "settings", _FakeUndoSettings(data_dir=tmp_path / "undo_data"))
    return workspace


def test_relative_path_resolves_inside_workspace(roots):
    workspace = roots
    resolved = filesystem._resolve_path("notes.txt")
    assert resolved == workspace / "notes.txt"


def test_relative_path_traversal_outside_workspace_is_allowed(roots):
    workspace = roots
    assert filesystem._resolve_path("../../other-project") == (workspace / "../../other-project").resolve()


def test_absolute_path_is_allowed(roots, tmp_path):
    target = tmp_path / "anywhere" / "resume.docx"
    assert filesystem._resolve_path(str(target)) == target.resolve()


def test_write_then_read_round_trip(roots):
    filesystem._write_file({"path": "hello.txt", "content": "hi there"})
    result = filesystem._read_file({"path": "hello.txt"})
    assert result == "hi there"


def test_env_path_is_not_specially_blocked(roots, tmp_path):
    fake_env = (tmp_path / ".env").resolve()
    assert filesystem._resolve_path(str(fake_env)) == fake_env
