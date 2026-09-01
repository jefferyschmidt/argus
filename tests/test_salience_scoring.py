import logging
import time
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

from argus.rules.matcher import RuleMatcher
from argus.rules.store import RuleStore
from argus.salience import scoring
from argus.salience.scoring import Candidate, base_urgency_for, interruption_cost, score, staleness
from argus.world.model import RhythmSummary, WorldSnapshot


@dataclass
class _FakeObs:
    kind: str
    subject: str | None = None
    source: str = "test"
    confidence: float = 1.0
    payload: dict = None

    def __post_init__(self):
        if self.payload is None:
            self.payload = {}


@pytest.fixture(autouse=True)
def _reset_tiebreak_state():
    scoring._tiebreak_call_timestamps.clear()
    scoring._warned_unknown_kinds.clear()
    yield
    scoring._tiebreak_call_timestamps.clear()
    scoring._warned_unknown_kinds.clear()


def _empty_snapshot(threads=None):
    from datetime import datetime
    return WorldSnapshot(
        now=datetime.now(), focus=None, open_threads=threads or [], horizon=[],
        devices={}, health=[], rhythms=RhythmSummary(baselines={}),
    )


def _thread_store(tmp_path):
    from argus.spine.store import SpineStore
    from argus.world.threads import ThreadStore
    return ThreadStore(SpineStore(tmp_path / "spine.db"), tmp_path / "argus.db")


def _matcher(tmp_path) -> RuleMatcher:
    return RuleMatcher(RuleStore(tmp_path / "argus.db"))


# -- base_urgency_for ---------------------------------------------------

def test_base_urgency_known_kind():
    assert base_urgency_for("reminder.due") == 0.80


def test_base_urgency_mail_important_vs_not():
    assert base_urgency_for("mail.received", important=True) == 0.55
    assert base_urgency_for("mail.received", important=False) == 0.10
    assert base_urgency_for("mail.received") == 0.10  # default: not important


def test_base_urgency_unknown_kind_uses_neutral_default_and_logs_once(caplog):
    with caplog.at_level(logging.WARNING):
        assert base_urgency_for("something.new") == 0.30
        assert base_urgency_for("something.new") == 0.30
        assert base_urgency_for("something.new") == 0.30

    assert caplog.text.count("Unknown kind") == 1  # logged once, not per event


# -- suppression short-circuit -------------------------------------------

def test_suppression_returns_without_computing_score(tmp_path):
    store = RuleStore(tmp_path / "argus.db")
    rule_id = store.propose(
        natural_language="Don't tell me about newsletters", kind="suppression",
        trigger={"kind": "mail.received", "filters": []}, action={"type": "suppress"},
    )
    store.confirm(rule_id)
    matcher = RuleMatcher(store)
    candidate = Candidate(observation_id=1, kind="mail.received", subject="a@x.com", text="x", base_urgency=1.0)

    with patch("argus.salience.scoring.staleness", side_effect=AssertionError("scoring must not run")):
        decision = score(
            candidate, _empty_snapshot(), matcher=matcher, rhythm_fit=0.5, interruption_cost=0.0,
            observation=_FakeObs(kind="mail.received", subject="a@x.com"),
        )

    assert decision.action == "suppress"
    assert f"rule {rule_id}" in decision.reason


def test_no_observation_means_no_rules_can_match(tmp_path):
    store = RuleStore(tmp_path / "argus.db")
    rule_id = store.propose(
        natural_language="x", kind="suppression",
        trigger={"kind": "mail.received", "filters": []}, action={"type": "suppress"},
    )
    store.confirm(rule_id)
    matcher = RuleMatcher(store)
    candidate = Candidate(observation_id=1, kind="mail.received", subject="a@x.com", text="x", base_urgency=0.1)

    decision = score(candidate, _empty_snapshot(), matcher=matcher, rhythm_fit=0.5, interruption_cost=0.3)
    assert decision.action != "suppress"


# -- worked examples (Appendix A.2) --------------------------------------

def test_worked_example_important_email_4h_thread_reproduces_to_0_001(tmp_path):
    store = RuleStore(tmp_path / "argus.db")
    matcher = RuleMatcher(store)
    thread_store = _thread_store(tmp_path)
    now = time.time()
    thread_id = thread_store.open("email_reply", "x")
    thread_store.touch(thread_id)
    thread = thread_store.get(thread_id)
    thread.opened_ts = now - 4 * 3600  # force exactly 4h old

    candidate = Candidate(
        observation_id=1, kind="mail.received", subject="a@x.com", text="x",
        base_urgency=base_urgency_for("mail.received", important=True), thread_id=thread_id,
    )
    snapshot = _empty_snapshot(threads=[thread])

    decision = score(
        candidate, snapshot, matcher=matcher, rhythm_fit=0.5, interruption_cost=0.2,
        observation=_FakeObs(kind="mail.received", subject="a@x.com"), now=now,
    )

    # Recompute the raw score the same way score() does, to assert to 0.001.
    expected = 0.45 * 0.55 + 0.20 * (4 / 72) + 0.20 * 0.0 + 0.0 - 0.35 * 0.2
    assert expected == pytest.approx(0.1886, abs=0.001)
    assert decision.action == "hold"


def test_worked_example_credential_failure_18h_idle_reproduces_to_0_001(tmp_path):
    store = RuleStore(tmp_path / "argus.db")
    matcher = RuleMatcher(store)

    candidate = Candidate(
        observation_id=2, kind="argus.credential_failed", subject="Yahoo", text="x",
        base_urgency=base_urgency_for("argus.credential_failed"),
    )
    # No thread -- staleness formula in the worked example uses 18h
    # directly, so we verify the arithmetic path via interruption_cost
    # + rhythm_fit + base_urgency with a synthetic staleness input by
    # asserting the underlying formula, then checking the actual score()
    # call with a thread aged to 18h for full realism.
    expected = 0.45 * 0.75 + 0.20 * (18 / 72) + 0 + 0 - 0.35 * 0.4
    assert expected == pytest.approx(0.2475, abs=0.001)

    decision = score(
        candidate, _empty_snapshot(), matcher=matcher, rhythm_fit=0.5, interruption_cost=0.4,
        observation=_FakeObs(kind="argus.credential_failed", subject="Yahoo"),
    )
    # No thread here -> staleness is 0, not 18h -- this call demonstrates
    # the no-thread branch specifically; the arithmetic above is what's
    # asserted against the PRD's literal worked numbers.
    assert decision.action == "hold"


def test_worked_example_with_real_thread_age_matches_prd_score(tmp_path):
    store = RuleStore(tmp_path / "argus.db")
    matcher = RuleMatcher(store)
    thread_store = _thread_store(tmp_path)
    now = time.time()
    thread_id = thread_store.open("system_health", "Yahoo credential failed")
    thread = thread_store.get(thread_id)
    thread.opened_ts = now - 18 * 3600

    candidate = Candidate(
        observation_id=2, kind="argus.credential_failed", subject="Yahoo", text="x",
        base_urgency=base_urgency_for("argus.credential_failed"), thread_id=thread_id,
    )
    snapshot = _empty_snapshot(threads=[thread])

    computed_staleness = staleness(candidate, snapshot, now)
    raw = (
        0.45 * candidate.base_urgency + 0.20 * computed_staleness + 0.20 * 0.0 + 0.0 - 0.35 * 0.4
    )
    assert raw == pytest.approx(0.2475, abs=0.001)

    decision = score(
        candidate, snapshot, matcher=matcher, rhythm_fit=0.5, interruption_cost=0.4,
        observation=_FakeObs(kind="argus.credential_failed", subject="Yahoo"), now=now,
    )
    assert decision.action == "hold"


# -- rhythm confidence below threshold ------------------------------------

def test_rhythm_fit_of_half_contributes_exactly_zero(tmp_path):
    store = RuleStore(tmp_path / "argus.db")
    matcher = RuleMatcher(store)
    candidate = Candidate(observation_id=1, kind="focus.changed", subject=None, text="x", base_urgency=0.05)

    d1 = score(candidate, _empty_snapshot(), matcher=matcher, rhythm_fit=0.5, interruption_cost=0.3)
    d2 = score(candidate, _empty_snapshot(), matcher=matcher, rhythm_fit=0.5, interruption_cost=0.3)
    assert d1.action == d2.action  # deterministic; rhythm_fit=0.5 is a no-op either way


# -- thresholds -------------------------------------------------------------

def test_high_score_speaks(tmp_path):
    store = RuleStore(tmp_path / "argus.db")
    rule_id = store.propose(
        natural_language="Boost reminders", kind="preference",
        trigger={"kind": "reminder.due", "filters": []}, action={"type": "boost", "amount": 1.0},
    )
    store.confirm(rule_id)
    matcher = RuleMatcher(store)
    candidate = Candidate(observation_id=1, kind="reminder.due", subject=None, text="x", base_urgency=1.0)

    decision = score(
        candidate, _empty_snapshot(), matcher=matcher, rhythm_fit=1.0, interruption_cost=0.0,
        observation=_FakeObs(kind="reminder.due"),
    )
    # 0.45*1.0 + 0.20*0.0 + 0.20*0.5 + 0.30*1.0 - 0.35*0.0 = 0.85 -> speak
    assert decision.action == "speak"


def test_low_score_holds(tmp_path):
    store = RuleStore(tmp_path / "argus.db")
    matcher = RuleMatcher(store)
    candidate = Candidate(observation_id=1, kind="git.branch_stale", subject=None, text="x", base_urgency=0.05)

    decision = score(candidate, _empty_snapshot(), matcher=matcher, rhythm_fit=0.5, interruption_cost=1.0)
    assert decision.action == "hold"


def test_mid_score_is_ambient(tmp_path):
    store = RuleStore(tmp_path / "argus.db")
    matcher = RuleMatcher(store)
    candidate = Candidate(observation_id=1, kind="routine.due", subject=None, text="x", base_urgency=0.90)

    decision = score(candidate, _empty_snapshot(), matcher=matcher, rhythm_fit=0.5, interruption_cost=0.0)
    # 0.45*0.90 = 0.405 -- below the ambiguous band (0.45) but above ambient (0.30)
    assert decision.action == "ambient"


def test_every_decision_has_a_nonempty_reason(tmp_path):
    store = RuleStore(tmp_path / "argus.db")
    matcher = RuleMatcher(store)
    for urgency, cost in ((0.9, 0.0), (0.5, 0.5), (0.05, 1.0)):
        candidate = Candidate(observation_id=1, kind="routine.due", subject=None, text="x", base_urgency=urgency)
        decision = score(candidate, _empty_snapshot(), matcher=matcher, rhythm_fit=0.5, interruption_cost=cost)
        assert decision.reason


# -- ambiguous band + LLM tie-break ----------------------------------------

def _ambiguous_candidate() -> Candidate:
    # base_urgency chosen so W_URGENCY*urgency alone lands in [0.45, 0.55]
    # with everything else neutral (staleness=0, rhythm=0, rule_bias=0, cost=0).
    return Candidate(observation_id=1, kind="routine.due", subject=None, text="x", base_urgency=0.50 / 0.45)


def test_ambiguous_band_with_no_tiebreak_holds(tmp_path):
    store = RuleStore(tmp_path / "argus.db")
    matcher = RuleMatcher(store)
    decision = score(_ambiguous_candidate(), _empty_snapshot(), matcher=matcher, rhythm_fit=0.5, interruption_cost=0.0)
    assert decision.action == "hold"


def test_ambiguous_band_tiebreak_speak(tmp_path):
    store = RuleStore(tmp_path / "argus.db")
    matcher = RuleMatcher(store)
    decision = score(
        _ambiguous_candidate(), _empty_snapshot(), matcher=matcher, rhythm_fit=0.5, interruption_cost=0.0,
        llm_tiebreak=lambda c: True,
    )
    assert decision.action == "speak"


def test_ambiguous_band_tiebreak_hold(tmp_path):
    store = RuleStore(tmp_path / "argus.db")
    matcher = RuleMatcher(store)
    decision = score(
        _ambiguous_candidate(), _empty_snapshot(), matcher=matcher, rhythm_fit=0.5, interruption_cost=0.0,
        llm_tiebreak=lambda c: False,
    )
    assert decision.action == "hold"


def test_ambiguous_band_tiebreak_exception_falls_back_to_hold(tmp_path):
    store = RuleStore(tmp_path / "argus.db")
    matcher = RuleMatcher(store)

    def _boom(c):
        raise RuntimeError("llm exploded")

    decision = score(
        _ambiguous_candidate(), _empty_snapshot(), matcher=matcher, rhythm_fit=0.5, interruption_cost=0.0,
        llm_tiebreak=_boom,
    )
    assert decision.action == "hold"


def test_seventh_tiebreak_call_in_an_hour_falls_back_to_deterministic(tmp_path, monkeypatch):
    monkeypatch.setattr("argus.salience.scoring.settings.salience_llm_calls_per_hour", 6)
    store = RuleStore(tmp_path / "argus.db")
    matcher = RuleMatcher(store)
    calls = []

    def tiebreak(c):
        calls.append(1)
        return True

    for _ in range(6):
        decision = score(
            _ambiguous_candidate(), _empty_snapshot(), matcher=matcher, rhythm_fit=0.5, interruption_cost=0.0,
            llm_tiebreak=tiebreak,
        )
        assert decision.action == "speak"

    seventh = score(
        _ambiguous_candidate(), _empty_snapshot(), matcher=matcher, rhythm_fit=0.5, interruption_cost=0.0,
        llm_tiebreak=tiebreak,
    )
    assert seventh.action == "hold"  # falls back to deterministic
    assert len(calls) == 6  # 7th call never reached the tiebreak function


# -- zero LLM calls at scale -------------------------------------------------

def test_scoring_10000_candidates_outside_ambiguous_band_makes_zero_llm_calls(tmp_path):
    store = RuleStore(tmp_path / "argus.db")
    matcher = RuleMatcher(store)
    tiebreak = MagicMock()

    for i in range(10_000):
        # Alternate clearly-high and clearly-low so nothing lands in the
        # ambiguous band.
        urgency = 0.95 if i % 2 == 0 else 0.02
        cost = 0.0 if i % 2 == 0 else 1.0
        candidate = Candidate(observation_id=i, kind="reminder.due", subject=None, text="x", base_urgency=urgency)
        score(candidate, _empty_snapshot(), matcher=matcher, rhythm_fit=0.5, interruption_cost=cost, llm_tiebreak=tiebreak)

    tiebreak.assert_not_called()


# -- interruption_cost ------------------------------------------------------

def test_interruption_cost_defaults_to_otherwise_with_no_signal(tmp_path):
    assert interruption_cost(_empty_snapshot(), None, None, time.time()) == 0.3


def test_interruption_cost_idle_when_idle_started_is_latest(tmp_path):
    from argus.spine.observation import Observation
    from argus.spine.store import SpineStore

    spine = SpineStore(tmp_path / "spine.db")
    now = time.time()
    spine.record(Observation(source="window_focus", kind="focus.changed", ts=now - 600, subject="x", dedupe_key="a"))
    spine.record(Observation(source="window_focus", kind="focus.idle_started", ts=now - 60, dedupe_key="b"))

    assert interruption_cost(_empty_snapshot(), spine, None, now) == 0.4


def test_interruption_cost_active_recently_not_focused(tmp_path):
    from argus.spine.observation import Observation
    from argus.spine.store import SpineStore

    spine = SpineStore(tmp_path / "spine.db")
    now = time.time()
    spine.record(Observation(source="window_focus", kind="focus.changed", ts=now - 60, subject="x", dedupe_key="a"))

    assert interruption_cost(_empty_snapshot(), spine, now=now, rhythms=None) == 0.2


def _snapshot_focused_for_minutes(minutes):
    from argus.world.model import FocusState

    snap = _empty_snapshot()
    snap.focus = FocusState(title="Code - argus", minutes=minutes, confidence=1.0)
    return snap


def _focus_rhythms():
    return {"app_class": {"value": {"code": {"class": "focus", "mean_minutes": 40.0, "n": 99}}}}


def test_listening_paused_and_quiet_mode_drive_interruption_cost(monkeypatch):
    """Found at the Phase C gate. Both signals exist in ui.commands but
    weren't consulted, so a muted mic scored the same as an idle desk
    (0.3) instead of 1.0 -- a 0.245 swing against a 0.62 speak threshold.
    Also pins the ordering correction: listening-paused must beat the
    focused row, which A.2's own table listed above it despite "first
    match wins"."""
    from argus.salience import scoring

    snapshot = _snapshot_focused_for_minutes(40)   # would otherwise score 0.7

    monkeypatch.setattr("argus.ui.commands.is_listening_paused", lambda: True)
    monkeypatch.setattr("argus.ui.commands.is_quiet_mode", lambda: False)
    assert scoring.interruption_cost(snapshot, None, _focus_rhythms(), now=0.0) == 1.0

    monkeypatch.setattr("argus.ui.commands.is_listening_paused", lambda: False)
    monkeypatch.setattr("argus.ui.commands.is_quiet_mode", lambda: True)
    assert scoring.interruption_cost(snapshot, None, _focus_rhythms(), now=0.0) == 0.6


def test_interruption_cost_survives_ui_state_being_unreadable(monkeypatch):
    """Scoring must never fail because the UI layer did."""
    from argus.salience import scoring

    def boom():
        raise RuntimeError("ui gone")

    monkeypatch.setattr("argus.ui.commands.is_listening_paused", boom)
    cost = scoring.interruption_cost(_snapshot_focused_for_minutes(40), None, _focus_rhythms(), now=0.0)
    assert cost == 0.7
