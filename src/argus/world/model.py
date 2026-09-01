"""PRD.md §4.3. WorldSnapshot is the present, projected from the spine +
threads + rhythms; to_prompt_block() is what actually gets injected into
prompts, so it is hard-capped. WorldModel.snapshot() must be cheap enough
to call on every turn -- cached with a short TTL, invalidated early on
thread open/close."""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime

from argus.config import settings
from argus.spine.store import SpineStore
from argus.world.rhythms import RhythmStore
from argus.world.threads import Thread, ThreadStore

log = logging.getLogger(__name__)


@dataclass
class FocusState:
    title: str
    minutes: float
    confidence: float


@dataclass
class CalendarItem:
    summary: str
    start: str
    location: str = ""


@dataclass
class HealthIssue:
    subject: str
    description: str
    opened_ts: float


@dataclass
class RhythmSummary:
    # name -> {"confidence": float, "days_observed": int} -- the raw
    # histogram/ratio `value` blobs stay in RhythmStore; a prompt block
    # only ever needs to say how much to trust each baseline, not repeat
    # its numbers.
    baselines: dict = field(default_factory=dict)


@dataclass
class WorldSnapshot:
    now: datetime
    focus: FocusState | None
    open_threads: list[Thread]
    horizon: list[CalendarItem]
    devices: dict           # populated in Phase F; {} until then
    health: list[HealthIssue]
    rhythms: RhythmSummary

    def to_prompt_block(self, max_chars: int = 1200) -> str:
        """Most salient first; sections are dropped from the tail before
        anything is hard-truncated, and the result never exceeds
        max_chars regardless of input size (e.g. 500 open threads)."""
        sections = []

        if self.focus is not None:
            sections.append(f"Currently: {self.focus.title} ({self.focus.minutes:.0f} min)")

        if self.open_threads:
            lines = [f"- {t.title}" for t in self.open_threads[:10]]
            if len(self.open_threads) > 10:
                lines.append(f"- ...and {len(self.open_threads) - 10} more")
            sections.append("Open threads:\n" + "\n".join(lines))

        if self.horizon:
            lines = [f"- {c.summary} at {c.start}" for c in self.horizon[:10]]
            sections.append("Upcoming:\n" + "\n".join(lines))

        if self.health:
            lines = [f"- {h.subject}: {h.description}" for h in self.health[:10]]
            sections.append("Health issues:\n" + "\n".join(lines))

        # Rhythms are the least salient section -- background calibration
        # info, not something worth interrupting for -- so it's first to
        # go when the budget is tight.
        confident = {name: b for name, b in self.rhythms.baselines.items() if b.get("confidence", 0.0) > 0}
        if confident:
            lines = [f"- {name}: confidence {b['confidence']:.2f}" for name, b in confident.items()]
            sections.append("Rhythms:\n" + "\n".join(lines))

        text = "\n\n".join(sections)
        while sections and len(text) > max_chars:
            sections.pop()
            text = "\n\n".join(sections)

        return text[:max_chars]


class WorldModel:
    def __init__(self, spine: SpineStore, threads: ThreadStore, rhythms: RhythmStore):
        self.spine = spine
        self.threads = threads
        self.rhythms = rhythms
        self._cached: WorldSnapshot | None = None
        self._cached_at: float = 0.0

    def invalidate(self) -> None:
        self._cached = None

    def open_thread(self, *args, **kwargs) -> int:
        """Thin pass-through to ThreadStore.open() that also invalidates
        the cached snapshot -- PRD §4.3: "invalidated on thread
        open/close." Kept here rather than on ThreadStore itself, which
        has no notion of a cache to invalidate."""
        thread_id = self.threads.open(*args, **kwargs)
        self.invalidate()
        return thread_id

    def close_thread(self, thread_id: int, reason: str) -> bool:
        closed = self.threads.close(thread_id, reason)
        if closed:
            self.invalidate()
        return closed

    def snapshot(self) -> WorldSnapshot:
        now = time.time()
        if self._cached is not None and (now - self._cached_at) < settings.world_snapshot_ttl_seconds:
            return self._cached
        snap = self._build_snapshot(now)
        self._cached = snap
        self._cached_at = now
        return snap

    def _build_snapshot(self, now: float) -> WorldSnapshot:
        return WorldSnapshot(
            now=datetime.fromtimestamp(now),
            focus=self._focus_state(now),
            open_threads=self.threads.open_threads(limit=500),
            horizon=self._calendar_horizon(now),
            devices={},
            health=self._health_issues(),
            rhythms=self._rhythm_summary(),
        )

    def _focus_state(self, now: float) -> FocusState | None:
        obs = self.spine.latest("focus.changed")
        if obs is None or obs.subject is None:
            return None
        return FocusState(title=obs.subject, minutes=max(0.0, (now - obs.ts) / 60), confidence=obs.confidence)

    def _calendar_horizon(self, now: float) -> list[CalendarItem]:
        cutoff = now + settings.horizon_hours * 3600
        observations = self.spine.query(kinds=["calendar.event_upcoming"], limit=500)
        seen_ids = set()
        items = []
        for obs in observations:
            event_id = obs.payload.get("id")
            if event_id is not None:
                if event_id in seen_ids:
                    continue  # newest-first query -- first occurrence per id is the latest report
                seen_ids.add(event_id)
            start_ts = _parse_iso(obs.payload.get("start"))
            if start_ts is None or start_ts > cutoff:
                continue
            items.append(CalendarItem(
                summary=obs.payload.get("summary", "(no title)"),
                start=obs.payload.get("start", ""),
                location=obs.payload.get("location", ""),
            ))
        items.sort(key=lambda c: c.start)
        return items

    def _health_issues(self) -> list[HealthIssue]:
        return [
            HealthIssue(subject=t.subject or "", description=t.title, opened_ts=t.opened_ts)
            for t in self.threads.open_threads(kind="system_health", limit=100)
        ]

    def _rhythm_summary(self) -> RhythmSummary:
        baselines = {}
        for name in ("active_hours", "app_class", "sender_importance", "session_length"):
            row = self.rhythms.get(name)
            if row is not None:
                baselines[name] = {"confidence": row["confidence"], "days_observed": row["days_observed"]}
        return RhythmSummary(baselines=baselines)


def _parse_iso(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None
