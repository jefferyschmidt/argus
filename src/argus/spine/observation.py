"""PRD.md §3.1 -- the perception layer's one durable record type. `kind`
uses a fixed, discoverable vocabulary (KINDS below) rather than a free
string, so every emitter and every reader agree on what event types
exist."""

from dataclasses import dataclass, field

KINDS = frozenset({
    "focus.changed", "focus.idle_started", "focus.idle_ended",
    "mail.received", "mail.deleted",
    "calendar.event_upcoming", "calendar.event_changed",
    "reminder.due", "routine.due",
    "git.commit", "git.branch_stale",
    "argus.integration_failed", "argus.credential_failed", "argus.spend_recorded",
    "task.started", "task.progress", "task.finished", "task.failed",
    "document.composed",
    "thread.opened", "thread.closed",
})


@dataclass(frozen=True)
class Observation:
    source: str              # emitting sensor, e.g. "window_focus", "mail", "argus.health"
    kind: str                # dotted event type, e.g. "focus.changed", "mail.received"
    ts: float                # unix epoch seconds, UTC
    subject: str | None = None   # the entity this is about (sender, window title, repo name)
    payload: dict = field(default_factory=dict)   # JSON-serializable, source-specific
    confidence: float = 1.0  # 0.0-1.0; sensors that infer rather than measure set this lower
    dedupe_key: str | None = None
