"""PRD.md §5.1-5.2 + Appendix A.2 (which replaces §5.2's prose -- built
from there). One arbiter replacing seven independent "should I speak?"
judgments, deterministic first: score() itself never calls an LLM. The
one exception -- an ambiguous-band tie-break -- is a caller-supplied
callable, capped per hour, so scoring 10,000 ordinary candidates still
makes zero LLM calls (A.2 acceptance).

Interruption *budget* (the persisted token bucket) is deliberately not
here -- Appendix A.2: "Budget is checked after scoring, never folded
into the score." That's unit 16's salience/budget.py, wrapping score()'s
result."""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Literal

from argus.config import settings
from argus.rules.matcher import RuleMatcher
from argus.world.model import WorldSnapshot

log = logging.getLogger(__name__)


@dataclass
class Candidate:
    observation_id: int | None
    kind: str
    subject: str | None
    text: str                  # what would be said
    base_urgency: float        # 0.0-1.0 from a static per-kind table -- see base_urgency_for()
    thread_id: int | None = None


@dataclass
class EscalationStep:
    after_seconds: float
    channel: Literal["speak", "ambient", "push"]


@dataclass
class Decision:
    action: Literal["speak", "hold", "ambient", "push", "suppress"]
    reason: str                        # human-readable; powers "why did you do that?"
    escalation: list[EscalationStep] = field(default_factory=list)
    audience: str | None = None        # reserved; never set in Phases A-I


# -- base_urgency (Appendix A.2 static table) --------------------------------

_BASE_URGENCY = {
    "argus.credential_failed": 0.75,
    "argus.integration_failed": 0.60,
    "reminder.due": 0.80,
    "calendar.event_upcoming": 0.70,
    "task.finished": 0.45,
    "task.failed": 0.60,
    "document.composed": 0.40,
    "routine.due": 0.50,
    "git.branch_stale": 0.15,
    "focus.changed": 0.05,
}
_MAIL_IMPORTANT_URGENCY = 0.55
_MAIL_UNIMPORTANT_URGENCY = 0.10
_UNKNOWN_KIND_URGENCY = 0.30

_warned_unknown_kinds: set[str] = set()


def base_urgency_for(kind: str, *, important: bool | None = None) -> float:
    """Resolves Appendix A.2's static per-kind table -- used to populate
    Candidate.base_urgency when a candidate is built (not read internally
    by score(), which trusts whatever the candidate already carries).
    mail.received is the one kind with two rows; `important` distinguishes
    them (Phase C's own triage decides that, not this function). Unknown
    kind uses the neutral default and logs once, not once per event."""
    if kind == "mail.received":
        return _MAIL_IMPORTANT_URGENCY if important else _MAIL_UNIMPORTANT_URGENCY
    value = _BASE_URGENCY.get(kind)
    if value is not None:
        return value
    if kind not in _warned_unknown_kinds:
        log.warning("Unknown kind %r for base_urgency -- using the neutral default %.2f", kind, _UNKNOWN_KIND_URGENCY)
        _warned_unknown_kinds.add(kind)
    return _UNKNOWN_KIND_URGENCY


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


# -- staleness ----------------------------------------------------------

def staleness(candidate: Candidate, snapshot: WorldSnapshot, now: float) -> float:
    """0.0 with no thread; otherwise the thread's age normalized against
    settings.staleness_saturation_hours (Appendix A.2)."""
    if candidate.thread_id is None:
        return 0.0
    thread = next((t for t in snapshot.open_threads if t.id == candidate.thread_id), None)
    if thread is None:
        return 0.0
    age_hours = (now - thread.opened_ts) / 3600
    return min(1.0, age_hours / settings.staleness_saturation_hours)


# -- interruption_cost ---------------------------------------------------
# Appendix A.2's table, first match wins. One row still has no signal
# source in this build and is structurally present but unreachable: "on
# a call" (no call sensor until Phase F). A future signal slots straight
# in.
#
# "in a meeting" IS wired, below -- the pre-U-C4 binding requirement in
# PRD §5.2: CalendarSensor/google_calendar now carry an event's end time
# (google_calendar.list_upcoming_events, world/model.py::CalendarItem.end),
# so interruption_cost can tell an ongoing calendar event from a merely
# upcoming one.
#
# Quiet mode and listening-paused are also wired. They were initially
# left out on the reasoning that they live in voice/UI state this module
# has no reason to import -- a fair instinct, but wrong on the merits: a
# module deciding whether to speak has every reason to know whether the
# mic is off. Unwired, both fell through to "otherwise" (0.3) instead of
# 1.0/0.6, a 0.245 score swing against a 0.62 threshold, i.e. Argus
# interrupting while explicitly muted.

def interruption_cost(snapshot: WorldSnapshot, spine, rhythms, now: float) -> float:
    # Checked first, matching Appendix A.2's literal table order -- a
    # calendar event in progress is as strong a signal as the mic being
    # off, so it shouldn't be shadowed by a check below it.
    if _in_a_meeting(snapshot, now):
        return 1.0

    # Checked before the focus row, correcting A.2's own table order: the
    # table says "first match wins" but lists listening-paused (1.0) below
    # focused (0.7), so a focused user with the mic off scored 0.7 instead
    # of 1.0. Every 1.0 row has to precede the weaker ones for "first
    # match wins" to mean anything.
    #
    # Imported inside the function, not at module scope, so tests can
    # patch it without process-global UI state leaking between them; and
    # wrapped, because scoring must never fail on account of the UI (repo
    # convention: optional things fail soft).
    try:
        from argus.ui import commands as ui_commands

        if ui_commands.is_listening_paused():
            return 1.0
        if ui_commands.is_quiet_mode():
            return 0.6
    except Exception:
        log.exception("Could not read UI state for interruption_cost -- treating as neither")

    if snapshot.focus is not None and snapshot.focus.minutes >= 25:
        app_row = (rhythms.get("app_class") or {}).get("value", {}).get(_app_key(snapshot.focus.title)) if rhythms else None
        if app_row and app_row.get("class") == "focus":
            return 0.7

    if spine is not None:
        idle_obs = spine.latest("focus.idle_started")
        ended_obs = spine.latest("focus.idle_ended")
        changed_obs = spine.latest("focus.changed")
        most_recent_active = max(
            (o for o in (ended_obs, changed_obs) if o is not None), key=lambda o: o.ts, default=None,
        )
        if idle_obs is not None and (most_recent_active is None or idle_obs.ts > most_recent_active.ts):
            return 0.4  # idle / away
        if most_recent_active is not None and (now - most_recent_active.ts) <= 300:
            return 0.2  # active in the last 5 min, not focused

    return 0.3  # otherwise


def _in_a_meeting(snapshot: WorldSnapshot, now: float) -> bool:
    """True if `now` falls within any calendar item's [start, end) --
    i.e. a meeting is actually in progress, not merely upcoming."""
    for item in snapshot.horizon:
        start_ts = _parse_calendar_ts(item.start)
        end_ts = _parse_calendar_ts(item.end)
        if start_ts is not None and end_ts is not None and start_ts <= now < end_ts:
            return True
    return False


def _parse_calendar_ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _app_key(title: str) -> str:
    for sep in (" - ", " — "):
        if sep in title:
            title = title.split(sep, 1)[0]
            break
    return title.strip().lower()


# -- rule_bias ------------------------------------------------------------

def _rule_bias(matched_rules: list) -> float:
    total = sum(r.action.get("amount", 0.0) for r in matched_rules if r.action.get("type") == "boost")
    return max(-1.0, min(1.0, total))


# -- LLM tie-break per-hour cap -------------------------------------------

_tiebreak_call_timestamps: list[float] = []


def _tiebreak_allowed(now: float) -> bool:
    cutoff = now - 3600
    _tiebreak_call_timestamps[:] = [t for t in _tiebreak_call_timestamps if t > cutoff]
    return len(_tiebreak_call_timestamps) < settings.salience_llm_calls_per_hour


def _record_tiebreak_call(now: float) -> None:
    _tiebreak_call_timestamps.append(now)


# -- score ------------------------------------------------------------------

def score(
    candidate: Candidate, snapshot: WorldSnapshot, *,
    matcher: RuleMatcher, rhythm_fit: float, interruption_cost: float,
    observation=None, llm_tiebreak: Callable[[Candidate], bool | None] | None = None,
    now: float | None = None,
) -> Decision:
    """Appendix A.2. `rhythm_fit` and `interruption_cost` are precomputed
    by the caller (RhythmStore.rhythm_fit(...) / this module's
    interruption_cost(...)) so this function is a pure, exactly-
    reproducible implementation of the formula -- not a place that
    reaches into the spine/rhythms itself.

    `observation` is the underlying spine Observation, needed for rule
    matching (RuleMatcher operates on Observations, which carry
    source/confidence/payload that Candidate deliberately doesn't). If
    omitted, no rules can match -- a conservative default, not a crash."""
    decision, _final_score = score_with_value(
        candidate, snapshot, matcher=matcher, rhythm_fit=rhythm_fit, interruption_cost=interruption_cost,
        observation=observation, llm_tiebreak=llm_tiebreak, now=now,
    )
    return decision


def score_with_value(
    candidate: Candidate, snapshot: WorldSnapshot, *,
    matcher: RuleMatcher, rhythm_fit: float, interruption_cost: float,
    observation=None, llm_tiebreak: Callable[[Candidate], bool | None] | None = None,
    now: float | None = None,
) -> tuple[Decision, float]:
    """Same as score(), but also returns the numeric 0..1 score --
    Decision itself deliberately carries no such field (PRD §5.1's exact
    shape), but held_items (§5.4) has a `score REAL` column, so whoever
    persists a held candidate needs the number too."""
    now = now if now is not None else time.time()

    matched_rules = matcher.match(observation, candidate.observation_id) if observation is not None else []

    # Step 1 -- suppression short-circuits, before any scoring. Never
    # outvoted by urgency: this ordering is not negotiable (Appendix A.2).
    for rule in matched_rules:
        if rule.action.get("type") == "suppress":
            decision = Decision(action="suppress", reason=f"suppressed by rule {rule.id}: {rule.natural_language}")
            return decision, 0.0

    # Step 2 -- the formula.
    rhythm_term = rhythm_fit - 0.5
    rule_bias = _rule_bias(matched_rules)
    raw_score = (
        settings.salience_weight_urgency * candidate.base_urgency
        + settings.salience_weight_staleness * staleness(candidate, snapshot, now)
        + settings.salience_weight_rhythm * rhythm_term
        + settings.salience_weight_rule * rule_bias
        - settings.salience_weight_cost * interruption_cost
    )
    final_score = _clamp01(raw_score)

    # Step 3 -- thresholds.
    if final_score >= settings.speak_threshold:
        action = "speak"
        reason = f"score {final_score:.3f} >= speak threshold {settings.speak_threshold}"
    elif settings.salience_ambiguous_band_low <= final_score <= settings.salience_ambiguous_band_high:
        action, reason = _ambiguous_band_decision(candidate, final_score, llm_tiebreak, now)
    elif final_score >= settings.ambient_threshold:
        action = "ambient"
        reason = f"score {final_score:.3f} >= ambient threshold {settings.ambient_threshold}"
    else:
        action = "hold"
        reason = f"score {final_score:.3f} below ambient threshold {settings.ambient_threshold}"

    return Decision(action=action, reason=reason), final_score


def _ambiguous_band_decision(
    candidate: Candidate, final_score: float, llm_tiebreak: Callable[[Candidate], bool | None] | None, now: float,
) -> tuple[str, str]:
    if llm_tiebreak is not None and _tiebreak_allowed(now):
        _record_tiebreak_call(now)
        try:
            verdict = llm_tiebreak(candidate)
        except Exception:
            log.exception("LLM tie-break failed; falling back to hold")
            verdict = None
        if verdict is True:
            return "speak", f"score {final_score:.3f} in ambiguous band; LLM tie-break said speak"
        if verdict is False:
            return "hold", f"score {final_score:.3f} in ambiguous band; LLM tie-break said hold"
        return "hold", f"score {final_score:.3f} in ambiguous band; LLM tie-break returned no verdict"
    return "hold", f"score {final_score:.3f} in ambiguous band; no tie-break available (cap or none configured)"
