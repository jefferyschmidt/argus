import json
import logging
import queue
import threading
from datetime import datetime, timezone

_lock = threading.Lock()
_subscribers: list[queue.Queue] = []
_log = logging.getLogger(__name__)

# Every meaningful thing that happens -- conversation turns, state
# transitions (listening/thinking/speaking, addressee-gate verdicts via the
# "confirming" mode, tool confirm requests/resolutions, memory events --
# already flows through this single publish() call, so logging every event
# here to disk gives full session-review capability for free, no new
# instrumentation needed at each of the ~40 call sites. Answers directly:
# "would we have to write transcripts as well as debugging/action info" --
# no, this already-existing choke point covers both. One file per day
# (data/events/events-YYYY-MM-DD.jsonl) so it doesn't grow unbounded.
_EVENT_LOG_ENABLED = True


def _event_log_path():
    from argus.config import settings

    d = settings.data_dir / "events"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"events-{datetime.now().strftime('%Y-%m-%d')}.jsonl"


def _append_to_event_log(event: dict) -> None:
    if not _EVENT_LOG_ENABLED:
        return
    try:
        record = {"ts": datetime.now(timezone.utc).isoformat(), **event}
        with open(_event_log_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception:
        # Best-effort, same as the rest of this module -- logging must
        # never be a hard dependency for Argus to keep working.
        _log.exception("Failed to append event to the session event log")


def publish(event: dict) -> None:
    """Fire-and-forget from any thread (voice loop, orchestrator, tool
    registry are all synchronous). No-op if nothing is listening -- the UI
    is optional, this must never be a hard dependency for Argus to work.
    Always also appended to the on-disk event log regardless of whether a
    UI is connected, so a session run headless is still fully reviewable
    afterward."""
    _append_to_event_log(event)
    with _lock:
        subs = list(_subscribers)
    for q in subs:
        q.put_nowait(event)


def subscribe() -> queue.Queue:
    q: queue.Queue = queue.Queue()
    with _lock:
        _subscribers.append(q)
    return q


def unsubscribe(q: queue.Queue) -> None:
    with _lock:
        if q in _subscribers:
            _subscribers.remove(q)


def has_subscribers() -> bool:
    with _lock:
        return len(_subscribers) > 0
