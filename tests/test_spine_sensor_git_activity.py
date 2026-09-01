import subprocess

import pytest

from argus.spine.sensors.git_activity import GitActivitySensor


def _init_repo(path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / "file.txt").write_text("hello")
    subprocess.run(["git", "add", "file.txt"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial commit"], cwd=path, check=True)


@pytest.fixture
def repo(tmp_path):
    _init_repo(tmp_path)
    return tmp_path


def test_no_watched_repos_returns_empty(monkeypatch):
    monkeypatch.setattr("argus.spine.sensors.git_activity.settings.watched_repos", [])
    assert GitActivitySensor().poll() == []


def test_commit_is_reported_with_hash_and_dedupe_key(monkeypatch, repo):
    monkeypatch.setattr("argus.spine.sensors.git_activity.settings.watched_repos", [str(repo)])
    observations = GitActivitySensor().poll()

    commits = [o for o in observations if o.kind == "git.commit"]
    assert len(commits) == 1
    assert commits[0].subject == str(repo)
    assert commits[0].payload["hash"]
    assert commits[0].dedupe_key == f"git.commit:{repo}:{commits[0].payload['hash']}"


def test_same_head_is_not_reported_twice(monkeypatch, repo):
    monkeypatch.setattr("argus.spine.sensors.git_activity.settings.watched_repos", [str(repo)])
    sensor = GitActivitySensor()
    first = [o for o in sensor.poll() if o.kind == "git.commit"]
    second = [o for o in sensor.poll() if o.kind == "git.commit"]

    assert len(first) == 1
    assert first[0].dedupe_key == second[0].dedupe_key  # store-level dedupe would suppress the repeat


def test_fresh_commit_is_not_flagged_stale(monkeypatch, repo):
    monkeypatch.setattr("argus.spine.sensors.git_activity.settings.watched_repos", [str(repo)])
    monkeypatch.setattr("argus.spine.sensors.git_activity.settings.git_stale_branch_days", 14.0)
    observations = GitActivitySensor().poll()
    assert [o for o in observations if o.kind == "git.branch_stale"] == []


def test_old_commit_is_flagged_stale(monkeypatch, repo):
    # Backdate the commit so it's well past the staleness threshold.
    subprocess.run(
        ["git", "commit", "--amend", "-q", "--no-edit", "--date=2000-01-01T00:00:00"],
        cwd=repo, check=True, env=_env_with_committer_date("2000-01-01T00:00:00"),
    )
    monkeypatch.setattr("argus.spine.sensors.git_activity.settings.watched_repos", [str(repo)])
    monkeypatch.setattr("argus.spine.sensors.git_activity.settings.git_stale_branch_days", 14.0)

    observations = GitActivitySensor().poll()

    stale = [o for o in observations if o.kind == "git.branch_stale"]
    assert len(stale) == 1
    assert stale[0].payload["age_days"] >= 14.0


def _env_with_committer_date(iso_date: str) -> dict:
    import os
    env = dict(os.environ)
    env["GIT_COMMITTER_DATE"] = iso_date
    return env


def test_nonexistent_repo_does_not_raise(monkeypatch):
    monkeypatch.setattr("argus.spine.sensors.git_activity.settings.watched_repos", ["C:/does/not/exist"])
    assert GitActivitySensor().poll() == []
