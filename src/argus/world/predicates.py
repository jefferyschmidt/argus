"""PRD.md Appendix A.1. One evaluator serves both threads.close_condition
(§4.1) and rule_instances/rules.until_condition (§7.1). Pure and
deterministic: no LLM, no side effects.

Failing open is deliberate (Appendix A.1): an unknown `type` logs a
warning and returns False, so a malformed predicate leaves a thread open
rather than silently closing it. A thread that should have closed and
didn't is visible and annoying; a thread that closes when it shouldn't
loses the item silently -- the exact defect Phase C exists to remove.

`thread` is typed as a Protocol (ThreadLike) rather than importing
world.threads.Thread -- this module has no need of ThreadStore itself,
only of a thread-shaped object with id/subject/opened_ts, and unit 8's
Thread dataclass satisfies it structurally without either module
depending on the other's import order."""

import logging
from typing import Protocol

from argus.spine.store import SpineStore

log = logging.getLogger(__name__)

_COMPARISON_OPS = {
    "gt": lambda a, b: a > b,
    "gte": lambda a, b: a >= b,
    "lt": lambda a, b: a < b,
    "lte": lambda a, b: a <= b,
    "eq": lambda a, b: a == b,
    "neq": lambda a, b: a != b,
}


class ThreadLike(Protocol):
    id: int
    subject: str | None
    opened_ts: float


def evaluate(predicate: dict, *, thread: ThreadLike | None, spine: SpineStore, now: float) -> bool:
    """Total: an unknown `type` logs a warning and returns False -- never
    raises, never guesses (Appendix A preamble)."""
    ptype = predicate.get("type")
    handler = _HANDLERS.get(ptype)
    if handler is None:
        log.warning("Unknown predicate type %r -- treating as not satisfied", ptype)
        return False
    return handler(predicate, thread, spine, now)


def _resolve_thread_subject(value, thread: ThreadLike | None):
    if value == "$thread.subject":
        return thread.subject if thread is not None else None
    return value


def _resolve_thread_opened_ts(value, thread: ThreadLike | None):
    if value == "$thread.opened_ts":
        return thread.opened_ts if thread is not None else None
    return value


def _resolve_field(obs, field: str):
    """Dotted path against an Observation -- top-level attribute, or
    `payload.<key>` (nested allowed). Unresolvable returns None."""
    parts = field.split(".")
    if parts[0] == "payload":
        value = obs.payload
        for part in parts[1:]:
            if not isinstance(value, dict) or part not in value:
                return None
            value = value[part]
        return value
    if len(parts) == 1 and hasattr(obs, parts[0]):
        return getattr(obs, parts[0])
    return None


# 1. An observation of this kind has been recorded since the thread opened.
def _observation_seen(predicate, thread, spine, now) -> bool:
    kind = predicate.get("kind")
    if not kind:
        return False
    subject = _resolve_thread_subject(predicate.get("subject"), thread)
    since = _resolve_thread_opened_ts(predicate.get("since"), thread)
    return len(spine.query(kinds=[kind], subject=subject, since=since, limit=1)) > 0


# 2. The user explicitly acknowledged this thread.
def _user_acknowledged(predicate, thread, spine, now) -> bool:
    if thread is None:
        return False
    observations = spine.query(kinds=["thread.acknowledged"], since=thread.opened_ts, limit=1000)
    return any(obs.payload.get("thread_id") == thread.id for obs in observations)


# 3. Wall-clock timeout measured from thread open.
def _timeout(predicate, thread, spine, now) -> bool:
    seconds = predicate.get("seconds")
    if thread is None or not isinstance(seconds, (int, float)):
        return False
    return (now - thread.opened_ts) >= seconds


# 4. Never closes on its own; only an explicit close() call.
def _manual_only(predicate, thread, spine, now) -> bool:
    return False


# 5. A named thread's state -- backed by the thread.closed observation
# ThreadStore emits when it closes a thread (§4.1/§3.1 vocabulary), not a
# direct ThreadStore lookup: evaluate() only has spine access.
def _thread_closed(predicate, thread, spine, now) -> bool:
    thread_id = predicate.get("thread_id")
    if thread_id is None:
        return False
    observations = spine.query(kinds=["thread.closed"], limit=1000)
    return any(obs.payload.get("thread_id") == thread_id for obs in observations)


# 6. Numeric comparison against the payload of the newest observation of a kind.
def _value_threshold(predicate, thread, spine, now) -> bool:
    kind = predicate.get("kind")
    op = predicate.get("op")
    value = predicate.get("value")
    field = predicate.get("field")
    if not kind or not field or not isinstance(value, (int, float)):
        return False
    if op not in _COMPARISON_OPS:
        log.warning("Unknown value_threshold op %r -- treating as not satisfied", op)
        return False
    obs = spine.latest(kind)
    if obs is None:
        return False
    actual = _resolve_field(obs, field)
    if not isinstance(actual, (int, float)):
        return False
    return _COMPARISON_OPS[op](actual, value)


# 7. Composition.
def _any_of(predicate, thread, spine, now) -> bool:
    predicates = predicate.get("predicates")
    if not isinstance(predicates, list):
        return False
    return any(evaluate(p, thread=thread, spine=spine, now=now) for p in predicates)


def _all_of(predicate, thread, spine, now) -> bool:
    predicates = predicate.get("predicates")
    if not isinstance(predicates, list):
        return False
    return all(evaluate(p, thread=thread, spine=spine, now=now) for p in predicates)


_HANDLERS = {
    "observation_seen": _observation_seen,
    "user_acknowledged": _user_acknowledged,
    "timeout": _timeout,
    "manual_only": _manual_only,
    "thread_closed": _thread_closed,
    "value_threshold": _value_threshold,
    "any_of": _any_of,
    "all_of": _all_of,
}
