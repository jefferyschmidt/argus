from dataclasses import dataclass
from pathlib import Path

import pytest

from argus import backup


@dataclass
class _FakeSettings:
    data_dir: Path
    workspace_dir: Path


@pytest.fixture
def fake_data_dir(tmp_path, monkeypatch):
    """Never touch the real data_dir/workspace_dir in tests -- restore_backup
    extracts unconditionally into whatever settings.data_dir resolves to,
    and the real one could be a live, in-use database. data_dir/
    workspace_dir are read-only properties on the real Settings, so swap
    the whole settings object backup.py sees rather than trying to patch
    individual attributes on it."""
    data_dir = tmp_path / "data"
    workspace_dir = data_dir / "workspace"
    data_dir.mkdir()
    workspace_dir.mkdir()
    monkeypatch.setattr(backup, "settings", _FakeSettings(data_dir=data_dir, workspace_dir=workspace_dir))
    return data_dir, workspace_dir


def test_backup_and_restore_round_trip(fake_data_dir, tmp_path):
    data_dir, workspace_dir = fake_data_dir
    (data_dir / "argus.db").write_text("fake sqlite content")
    (workspace_dir / "notes.txt").write_text("hello from the workspace")

    backup_path = tmp_path / "backup.enc"
    result = backup.create_backup(backup_path, "correct-horse-battery-staple")
    assert result["entries"] == 2
    assert backup_path.exists()

    # Wipe the "current" data before restoring, to prove restore actually
    # recreates it rather than the test just seeing leftover files.
    (data_dir / "argus.db").unlink()
    (workspace_dir / "notes.txt").unlink()

    restore_result = backup.restore_backup(backup_path, "correct-horse-battery-staple")
    assert restore_result["entries"] == 2
    assert (data_dir / "argus.db").read_text() == "fake sqlite content"
    assert (workspace_dir / "notes.txt").read_text() == "hello from the workspace"


def test_backup_file_is_not_plaintext(fake_data_dir, tmp_path):
    data_dir, _ = fake_data_dir
    (data_dir / "argus.db").write_text("a secret you shouldn't be able to read")

    backup_path = tmp_path / "backup.enc"
    backup.create_backup(backup_path, "some-passphrase")

    raw = backup_path.read_bytes()
    assert b"secret you shouldn't be able to read" not in raw


def test_wrong_passphrase_is_rejected(fake_data_dir, tmp_path):
    data_dir, _ = fake_data_dir
    (data_dir / "argus.db").write_text("content")

    backup_path = tmp_path / "backup.enc"
    backup.create_backup(backup_path, "correct-passphrase")

    with pytest.raises(backup.WrongPassphraseOrCorruptBackup):
        backup.restore_backup(backup_path, "wrong-passphrase")


def test_backup_with_nothing_to_back_up_produces_zero_entries(fake_data_dir, tmp_path):
    backup_path = tmp_path / "backup.enc"
    result = backup.create_backup(backup_path, "passphrase")
    assert result["entries"] == 0
