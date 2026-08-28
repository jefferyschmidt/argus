import sys
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

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


def test_bare_relative_path_resolves_against_argus_own_source_root():
    """Confirmed live as a real bug: "ui" (unambiguously meant as
    src/argus/ui -- said right after list_own_source's own listing of the
    source root showed "ui/" as an entry) used to be resolved ONLY
    against PROJECT_ROOT, landing on a nonexistent top-level ui/ and
    getting refused as "outside" even though the intent was completely
    clear. Uses the REAL project layout (not the fake_roots fixture,
    which doesn't monkeypatch PROJECT_ROOT) since that's exactly the
    layout that triggered the bug."""
    result = si._list_own_source({"path": "ui"})
    assert not result.startswith("error:")
    assert "static/" in result


def test_bare_relative_file_path_resolves_against_argus_own_source_root():
    result = si._read_own_source({"path": "orchestrator.py"})
    assert not result.startswith("error:")
    assert "SYSTEM_PROMPT" in result


def test_already_prefixed_relative_path_still_works():
    result = si._list_own_source({"path": "src/argus/ui"})
    assert not result.startswith("error:")
    assert "static/" in result


def test_relative_tests_path_still_resolves_to_the_tests_root():
    result = si._list_own_source({"path": "tests"})
    assert not result.startswith("error:")
    assert "test_self_improve_tools.py" in result


def test_reading_a_directory_gives_a_clear_error_not_a_raw_permission_error():
    """Confirmed live as a real bug: reading a directory raised a raw
    Windows PermissionError from open(), surfaced verbatim as "permission
    error" -- confusing and wrong, since it's not a real permissions
    problem, just the wrong tool for a directory."""
    result = si._read_own_source({"path": "ui"})
    assert result.startswith("error:")
    assert "directory" in result
    assert "list_own_source" in result


def test_writing_to_a_directory_path_gives_a_clear_error(fake_roots):
    src_root, _ = fake_roots
    (src_root / "sub").mkdir()

    result = si._write_own_source({"path": str(src_root / "sub"), "content": "x"})

    assert result.startswith("error:")
    assert "directory" in result


def test_run_own_tests_uses_the_current_interpreter_not_bare_python():
    """Confirmed live as a real, currently-broken bug: the literal string
    "python" on PATH resolved to a completely different global install
    with no project dependencies (pytest included) -- run_own_tests
    reported FAILED unconditionally regardless of whether the actual code
    change was fine, silently defeating the self-improve system prompt's
    one safety check ("never claim success without having actually seen
    tests pass")."""
    with patch("argus.tools.self_improve.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "5 passed"
        mock_run.return_value.stderr = ""
        si._run_own_tests({})

    called_command = mock_run.call_args[0][0]
    assert called_command[0] == sys.executable
    assert called_command[0] != "python"
