import queue
import threading

_lock = threading.Lock()
_subscribers: list[queue.Queue] = []


def publish(event: dict) -> None:
    """Fire-and-forget from any thread (voice loop, orchestrator, tool
    registry are all synchronous). No-op if nothing is listening -- the UI
    is optional, this must never be a hard dependency for Argus to work."""
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
