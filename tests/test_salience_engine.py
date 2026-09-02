from dataclasses import dataclass
from datetime import datetime

from argus.rules.matcher import RuleMatcher
from argus.rules.store import RuleStore
from argus.salience.budget import InterruptionBudget
from argus.salience.engine import SalienceEngine
from argus.salience.held import HeldQueue
from argus.salience.scoring import Candidate
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


def _snapshot():
    return WorldSnapshot(now=datetime.now(), focus=None, open_threads=[], horizon=[], devices={}, health=[], rhythms=RhythmSummary())


def _engine(tmp_path, cap=3) -> SalienceEngine:
    matcher = RuleMatcher(RuleStore(tmp_path / "rules.db"))
    budget = InterruptionBudget(tmp_path / "budget.db")
    held = HeldQueue(tmp_path / "held.db")
    return SalienceEngine(matcher, budget, held)


def _speak_candidate() -> Candidate:
    # base_urgency alone (0.45*1.0=0.45) is below speak_threshold(0.62);
    # give it a boost via a matched rule instead, same trick as the
    # scoring worked-example test.
    return Candidate(observation_id=1, kind="reminder.due", subject=None, text="reminder!", base_urgency=1.0)


def test_speak_consumes_budget_and_is_not_held(tmp_path, monkeypatch):
    monkeypatch.setattr("argus.salience.budget.settings.interruptions_per_hour", 3)
    engine = _engine(tmp_path)
    rule_id = engine.matcher.store.propose(
        natural_language="boost reminders", kind="preference",
        trigger={"kind": "reminder.due", "filters": []}, action={"type": "boost", "amount": 1.0},
    )
    engine.matcher.store.confirm(rule_id)

    decision = engine.decide(_speak_candidate(), _snapshot(), observation=_FakeObs(kind="reminder.due"), now=1_000_000.0)

    assert decision.action == "speak"
    assert engine.budget.remaining(1_000_000.0) == 2
    assert engine.held.pending() == []


def test_speak_becomes_hold_when_budget_exhausted_and_lands_in_held_items(tmp_path, monkeypatch):
    """Phase C acceptance (§5.6): with budget exhausted, a speak
    candidate becomes hold and lands in held_items."""
    monkeypatch.setattr("argus.salience.budget.settings.interruptions_per_hour", 1)
    engine = _engine(tmp_path)
    rule_id = engine.matcher.store.propose(
        natural_language="boost reminders", kind="preference",
        trigger={"kind": "reminder.due", "filters": []}, action={"type": "boost", "amount": 1.0},
    )
    engine.matcher.store.confirm(rule_id)
    now = 1_000_000.0
    engine.budget.consume(now)  # exhaust the single slot

    decision = engine.decide(_speak_candidate(), _snapshot(), observation=_FakeObs(kind="reminder.due"), now=now)

    assert decision.action == "hold"
    assert "budget exhausted" in decision.reason
    pending = engine.held.pending()
    assert len(pending) == 1
    assert pending[0].kind == "reminder.due"


def test_ordinary_hold_is_queued_in_held_items(tmp_path):
    engine = _engine(tmp_path)
    candidate = Candidate(observation_id=1, kind="git.branch_stale", subject="repo-x", text="branch is stale", base_urgency=0.05)

    decision = engine.decide(candidate, _snapshot(), now=1_000_000.0)

    assert decision.action == "hold"
    pending = engine.held.pending()
    assert len(pending) == 1
    assert pending[0].subject == "repo-x"
    assert pending[0].score < 0.30


def test_ambient_is_not_queued_and_does_not_consume_budget(tmp_path, monkeypatch):
    monkeypatch.setattr("argus.salience.budget.settings.interruptions_per_hour", 3)
    engine = _engine(tmp_path)
    candidate = Candidate(observation_id=1, kind="routine.due", subject=None, text="x", base_urgency=0.90)

    decision = engine.decide(candidate, _snapshot(), now=1_000_000.0)

    assert decision.action == "ambient"
    assert engine.held.pending() == []
    assert engine.budget.remaining(1_000_000.0) == 3


def test_suppress_does_not_consume_budget_or_get_held(tmp_path, monkeypatch):
    monkeypatch.setattr("argus.salience.budget.settings.interruptions_per_hour", 3)
    engine = _engine(tmp_path)
    rule_id = engine.matcher.store.propose(
        natural_language="suppress mail", kind="suppression",
        trigger={"kind": "mail.received", "filters": []}, action={"type": "suppress"},
    )
    engine.matcher.store.confirm(rule_id)
    candidate = Candidate(observation_id=1, kind="mail.received", subject="a@x.com", text="x", base_urgency=1.0)

    decision = engine.decide(candidate, _snapshot(), observation=_FakeObs(kind="mail.received", subject="a@x.com"), now=1_000_000.0)

    assert decision.action == "suppress"
    assert engine.held.pending() == []
    assert engine.budget.remaining(1_000_000.0) == 3


# -- decision log (PRD §7.6) -------------------------------------------

def test_decide_logs_the_decision_when_a_decision_log_is_given(tmp_path):
    from argus.salience.decision_log import DecisionLog

    matcher = RuleMatcher(RuleStore(tmp_path / "rules.db"))
    budget = InterruptionBudget(tmp_path / "budget.db")
    held = HeldQueue(tmp_path / "held.db")
    decision_log = DecisionLog(tmp_path / "log.db")
    engine = SalienceEngine(matcher, budget, held, decision_log=decision_log)
    candidate = Candidate(observation_id=1, kind="git.branch_stale", subject="repo-x", text="x", base_urgency=0.05)

    decision = engine.decide(candidate, _snapshot(), now=1_000_000.0)

    last = decision_log.last()
    assert last.kind == "git.branch_stale"
    assert last.subject == "repo-x"
    assert last.action == decision.action
    assert last.reason == decision.reason


def test_decide_without_a_decision_log_does_not_raise(tmp_path):
    engine = _engine(tmp_path)  # no decision_log
    candidate = Candidate(observation_id=1, kind="git.branch_stale", subject=None, text="x", base_urgency=0.05)
    engine.decide(candidate, _snapshot(), now=1_000_000.0)  # must not raise
