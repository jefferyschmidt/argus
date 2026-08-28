from dataclasses import dataclass, field
from pathlib import Path

import pytest

from argus.tools import filesystem


@dataclass
class _FakeSettings:
    workspace_dir: Path
    real_fs_roots: list = field(default_factory=list)


@pytest.fixture
def roots(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    real_root = tmp_path / "Documents"
    real_root.mkdir()
    monkeypatch.setattr(filesystem, "_allowed_roots", lambda: [workspace, real_root.resolve()])
    monkeypatch.setattr(filesystem, "settings", _FakeSettings(workspace_dir=workspace))
    return workspace, real_root


def test_relative_path_resolves_inside_workspace(roots):
    workspace, _ = roots
    resolved = filesystem._resolve_path("notes.txt")
    assert resolved == workspace / "notes.txt"


def test_relative_path_traversal_outside_workspace_is_rejected(roots):
    with pytest.raises(filesystem.PathEscapesAllowedRoots):
        filesystem._resolve_path("../../etc/passwd")


def test_absolute_path_inside_real_root_is_allowed(roots):
    _, real_root = roots
    target = real_root / "resume.docx"
    resolved = filesystem._resolve_path(str(target))
    assert resolved == target.resolve()


def test_absolute_path_outside_all_roots_is_rejected(roots, tmp_path):
    outside = tmp_path / "somewhere_else" / "secret.txt"
    with pytest.raises(filesystem.PathEscapesAllowedRoots):
        filesystem._resolve_path(str(outside))


def test_write_then_read_round_trip(roots):
    filesystem._write_file({"path": "hello.txt", "content": "hi there"})
    result = filesystem._read_file({"path": "hello.txt"})
    assert result == "hi there"


def test_denied_path_is_rejected_even_inside_an_allowed_root(roots, monkeypatch):
    _, real_root = roots
    fake_env = (real_root / ".env").resolve()
    monkeypatch.setattr(filesystem, "_DENIED_PATHS", [fake_env])

    with pytest.raises(filesystem.PathIsDenied):
        filesystem._resolve_path(str(fake_env))
