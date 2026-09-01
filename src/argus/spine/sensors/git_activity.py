"""PRD.md §3.4. New (no existing worker does this): scans
settings.watched_repos for new commits and branches whose HEAD hasn't
moved in a while. Shells out to `git` rather than adding a library
dependency -- argus/tools/self_improve.py's commit_own_changes_tool does
the same."""

import logging
import subprocess
import time

from argus.config import settings
from argus.spine.observation import Observation
from argus.spine.sensors.base import Sensor

log = logging.getLogger(__name__)


class GitActivitySensor(Sensor):
    name = "git"
    interval_seconds = 300.0

    def poll(self) -> list[Observation]:
        observations = []
        for repo in settings.watched_repos:
            observations.extend(self._scan_repo(repo))
        return observations

    def _run_git(self, repo: str, *args: str) -> str | None:
        try:
            result = subprocess.run(
                ["git", *args], cwd=repo, capture_output=True, text=True, timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            log.exception("git %s failed in %s", " ".join(args), repo)
            return None
        if result.returncode != 0:
            return None
        return result.stdout.strip()

    def _scan_repo(self, repo: str) -> list[Observation]:
        observations = []
        now = time.time()
        commit_ts: float | None = None

        head = self._run_git(repo, "log", "-1", "--format=%H|%ct")
        if head and "|" in head:
            commit_hash, _, raw_ts = head.partition("|")
            commit_ts = float(raw_ts) if raw_ts else None
            observations.append(Observation(
                source=self.name, kind="git.commit", ts=now, subject=repo,
                payload={"hash": commit_hash, "committed_ts": commit_ts},
                dedupe_key=f"git.commit:{repo}:{commit_hash}",
            ))

        if commit_ts is not None:
            branch = self._run_git(repo, "rev-parse", "--abbrev-ref", "HEAD")
            age_days = (now - commit_ts) / 86400
            if branch and age_days >= settings.git_stale_branch_days:
                # Bucketed by day so a still-stale branch is re-reported
                # once daily, not suppressed forever after the first poll.
                day_bucket = int(now // 86400)
                observations.append(Observation(
                    source=self.name, kind="git.branch_stale", ts=now, subject=repo,
                    payload={"branch": branch, "age_days": age_days},
                    dedupe_key=f"git.branch_stale:{repo}:{branch}:{day_bucket}",
                ))

        return observations
