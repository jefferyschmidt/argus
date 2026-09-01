"""PRD.md Appendix A.4. Histograms and ratios only -- no ML, no
clustering, no libraries beyond the stdlib. Recomputed once daily over a
trailing settings.rhythm_window_days window; readers read the stored
row, never recomputing the four baselines on a hot path. `rhythm_fit()`
is the one exception -- it's plain arithmetic over an indexed spine
query (no LLM, no heavy aggregation), meant to be called per-candidate
by Phase C's scoring (Appendix A.2), so it isn't itself persisted."""

import json
import logging
import sqlite3
import statistics
import time
from collections import defaultdict
from datetime import datetime

from argus.config import settings
from argus.spine.store import SpineStore

log = logging.getLogger(__name__)

# Appendix A.4: "min_samples per baseline."
_MIN_SAMPLES = {
    "active_hours": 200,
    "app_class": 30,          # per app
    "sender_importance": 5,   # per sender
    "session_length": 20,
}
# rhythm_fit() has no named row in the `rhythms` table (it isn't one of
# the four persisted baselines) and so no min_samples given in Appendix
# A.4 either -- session_length's floor is the closest analog (both are
# per-kind behavioral-timing statistics), used here for its confidence
# calculation.
_RHYTHM_FIT_MIN_SAMPLES = 20

# "acted" reuses exactly the definition Appendix A.4 gives for
# sender_importance ("a mail.replied, mail.deleted, or thread.acknowledged
# observation referencing that sender") rather than inventing a
# per-kind-specific notion of "acted on" for rhythm_fit's generic kind K.
_ACTED_KINDS = ("mail.replied", "mail.deleted", "thread.acknowledged")


def _confidence(days_observed: int, samples: int, min_samples: int) -> float:
    """Appendix A.4 -- one formula for all four baselines (and reused by
    rhythm_fit). Below rhythm_min_days of history, or too few samples,
    the baseline must never be asserted as fact (ROADMAP.md P1)."""
    if days_observed < settings.rhythm_min_days:
        return 0.0
    return min(1.0, (days_observed / settings.rhythm_window_days) * min(1.0, samples / min_samples))


def _app_key(title: str) -> str:
    """Window title truncated at the first ' - ' / ' — ', lowercased."""
    for sep in (" - ", " — "):
        if sep in title:
            title = title.split(sep, 1)[0]
            break
    return title.strip().lower()


def _local_hour(ts: float) -> int:
    return datetime.fromtimestamp(ts).hour


def _local_date(ts: float):
    return datetime.fromtimestamp(ts).date()


def _focus_sessions(spine: SpineStore, since: float, until: float) -> list[tuple[str, float, float]]:
    """(app_key, duration_minutes, start_ts) for each contiguous span
    between one focus.changed observation and the next -- the shared
    basis for both app_class and session_length."""
    observations = sorted(
        spine.query(kinds=["focus.changed"], since=since, until=until, limit=1_000_000),
        key=lambda o: o.ts,
    )
    sessions = []
    for i, obs in enumerate(observations[:-1]):
        if not obs.subject:
            continue
        duration_minutes = (observations[i + 1].ts - obs.ts) / 60
        if duration_minutes > 0:
            sessions.append((_app_key(obs.subject), duration_minutes, obs.ts))
    return sessions


class RhythmStore:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get(self, name: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM rhythms WHERE name = ?", (name,)).fetchone()
        if row is None:
            return None
        return {
            "name": row["name"], "value": json.loads(row["value"]),
            "days_observed": row["days_observed"], "samples": row["samples"],
            "confidence": row["confidence"], "computed_ts": row["computed_ts"],
        }

    def _save(self, name: str, value: dict, days_observed: int, samples: int, confidence: float) -> None:
        self.conn.execute(
            "INSERT INTO rhythms (name, value, days_observed, samples, confidence, computed_ts) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET value = excluded.value, days_observed = excluded.days_observed, "
            "samples = excluded.samples, confidence = excluded.confidence, computed_ts = excluded.computed_ts",
            (name, json.dumps(value), days_observed, samples, confidence, time.time()),
        )
        self.conn.commit()

    def recompute(self, spine: SpineStore) -> None:
        """Never called on a hot path -- once daily, over the trailing
        window. Makes zero LLM calls (Appendix A.4 acceptance)."""
        now = time.time()
        since = now - settings.rhythm_window_days * 86400
        for name, compute in (
            ("active_hours", self._compute_active_hours),
            ("app_class", self._compute_app_class),
            ("sender_importance", self._compute_sender_importance),
            ("session_length", self._compute_session_length),
        ):
            value, days_observed, samples, confidence = compute(spine, since, now)
            self._save(name, value, days_observed, samples, confidence)

    # -- the four baselines (Appendix A.4) -------------------------------

    def _compute_active_hours(self, spine: SpineStore, since: float, until: float):
        observations = spine.query(since=since, until=until, limit=1_000_000)
        buckets = [0] * 24
        days = set()
        for obs in observations:
            buckets[_local_hour(obs.ts)] += 1
            days.add(_local_date(obs.ts))
        samples = sum(buckets)
        peak = max(buckets) if buckets else 0
        normalized = [b / peak for b in buckets] if peak else [0.0] * 24
        days_observed = len(days)
        confidence = _confidence(days_observed, samples, _MIN_SAMPLES["active_hours"])
        return {"buckets": normalized}, days_observed, samples, confidence

    def _compute_app_class(self, spine: SpineStore, since: float, until: float):
        sessions = _focus_sessions(spine, since, until)
        by_app: dict[str, list[float]] = defaultdict(list)
        days = set()
        for app_key, minutes, start_ts in sessions:
            by_app[app_key].append(minutes)
            days.add(_local_date(start_ts))

        value = {}
        total_samples = 0
        for app_key, durations in by_app.items():
            n = len(durations)
            total_samples += n
            if n < _MIN_SAMPLES["app_class"]:
                continue  # not enough sessions to receive a class
            mean_minutes = sum(durations) / n
            cls = "focus" if mean_minutes >= settings.focus_session_minutes else "browse"
            value[app_key] = {"class": cls, "mean_minutes": round(mean_minutes, 2), "n": n}

        days_observed = len(days)
        confidence = _confidence(days_observed, total_samples, _MIN_SAMPLES["app_class"])
        return value, days_observed, total_samples, confidence

    def _compute_sender_importance(self, spine: SpineStore, since: float, until: float):
        received = spine.query(kinds=["mail.received"], since=since, until=until, limit=1_000_000)
        acted = spine.query(kinds=list(_ACTED_KINDS), since=since, until=until, limit=1_000_000)

        acted_by_sender: dict[str, int] = defaultdict(int)
        for obs in acted:
            if obs.subject:
                acted_by_sender[obs.subject] += 1

        received_by_sender: dict[str, int] = defaultdict(int)
        days = set()
        for obs in received:
            if not obs.subject:
                continue
            received_by_sender[obs.subject] += 1
            days.add(_local_date(obs.ts))

        value = {}
        total_samples = 0
        for sender, n_received in received_by_sender.items():
            total_samples += n_received
            if n_received < _MIN_SAMPLES["sender_importance"]:
                continue
            n_acted = min(acted_by_sender.get(sender, 0), n_received)
            value[sender] = {"ratio": round(n_acted / n_received, 3), "n": n_received}

        days_observed = len(days)
        confidence = _confidence(days_observed, total_samples, _MIN_SAMPLES["sender_importance"])
        return value, days_observed, total_samples, confidence

    def _compute_session_length(self, spine: SpineStore, since: float, until: float):
        sessions = _focus_sessions(spine, since, until)
        durations = [minutes for _app_key, minutes, _ts in sessions]
        days = {_local_date(ts) for _app_key, _minutes, ts in sessions}

        median_minutes = statistics.median(durations) if durations else 0.0
        samples = len(durations)
        days_observed = len(days)
        confidence = _confidence(days_observed, samples, _MIN_SAMPLES["session_length"])
        return {"median_minutes": round(median_minutes, 2)}, days_observed, samples, confidence

    # -- consumption (Appendix A.2/A.4) ----------------------------------

    def rhythm_fit(self, kind: str, hour: int, spine: SpineStore) -> float:
        """For a candidate of `kind` at local `hour`: the fraction of
        `kind` observations that were acted on within that hour bucket,
        divided by the maximum such fraction across all buckets. Returns
        0.5 (neutral) whenever the supporting confidence is below
        settings.rhythm_min_confidence, so an unproven rhythm contributes
        nothing to scoring rather than a guess."""
        now = time.time()
        since = now - settings.rhythm_window_days * 86400
        observations = spine.query(kinds=[kind], since=since, until=now, limit=1_000_000)
        if not observations:
            return 0.5

        acted = spine.query(kinds=list(_ACTED_KINDS), since=since, until=now, limit=1_000_000)
        acted_subjects_by_hour: dict[int, set] = defaultdict(set)
        for obs in acted:
            if obs.subject:
                acted_subjects_by_hour[_local_hour(obs.ts)].add(obs.subject)

        per_hour_total = [0] * 24
        per_hour_acted = [0] * 24
        days = set()
        for obs in observations:
            h = _local_hour(obs.ts)
            per_hour_total[h] += 1
            days.add(_local_date(obs.ts))
            if obs.subject and obs.subject in acted_subjects_by_hour.get(h, set()):
                per_hour_acted[h] += 1

        samples = sum(per_hour_total)
        days_observed = len(days)
        confidence = _confidence(days_observed, samples, _RHYTHM_FIT_MIN_SAMPLES)
        if confidence < settings.rhythm_min_confidence:
            return 0.5

        fractions = [
            (per_hour_acted[h] / per_hour_total[h]) if per_hour_total[h] else 0.0
            for h in range(24)
        ]
        peak = max(fractions)
        if peak <= 0:
            return 0.5
        return fractions[hour] / peak
