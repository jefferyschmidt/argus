from dataclasses import dataclass
from pathlib import Path

import pytest

from argus import undo_log
from argus.tools import self_improve as si


@dataclass
class _FakeUndoSettings:
    data_dir: Path


@pytest.fixture
def fake_roots(tmp_path, monkeypatch):
    src_root = tmp_path / "src" / "argus"
    tests_root = tmp_path / "tests"
    src_root.mkdir(parents=True)
    tests_root.mkdir(parents=True)
    monkeypatch.setattr(si, "_SELF_ROOTS", [src_root.resolve(), tests_root.resolve()])
    # _write_own_source calls undo_log.snapshot_before_write, which reads
    # its own settings reference independently of _SELF_ROOTS above --
    # without this, tests here would write real entries into the live
    # data/undo/log.jsonl (same real pollution confirmed live in
    # test_filesystem.py's equivalent fixture).
    monkeypatch.setattr(undo_log, "settings", _FakeUndoSettings(data_dir=tmp_path / "undo_data"))
    return src_root, tests_root


def test_write_and_read_within_allowed_root(fake_roots):
    src_root, _ = fake_roots
    target = src_root / "foo.py"

    result = si._write_own_source({"path": str(target), "content": "print('hi')"})

    assert "wrote" in result
    assert target.read_text() == "print('hi')"
    assert si._read_own_source({"path": str(target)}) == "print('hi')"


def test_write_rejects_path_outside_allowed_roots(fake_roots, tmp_path):
    outside = tmp_path / "elsewhere.py"

    result = si._write_own_source({"path": str(outside), "content": "bad"})

    assert result.startswith("error:")
    assert not outside.exists()


def test_read_rejects_path_outside_allowed_roots(fake_roots, tmp_path):
    (tmp_path / "secret.env").write_text("API_KEY=xyz")

    result = si._read_own_source({"path": str(tmp_path / "secret.env")})

    assert result.startswith("error:")
    assert "xyz" not in result


def test_read_nonexistent_file_reports_error(fake_roots):
    src_root, _ = fake_roots
    result = si._read_own_source({"path": str(src_root / "missing.py")})
    assert result.startswith("error:")


def test_list_own_source(fake_roots):
    src_root, _ = fake_roots
    (src_root / "a.py").write_text("x")
    (src_root / "sub").mkdir()

    result = si._list_own_source({"path": str(src_root)})

    assert "a.py" in result
    assert "sub/" in result
