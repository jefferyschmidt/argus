import threading
from dataclasses import dataclass

from argus.memory.store import get_connection
from argus.rules.matcher import RuleMatcher
from argus.rules.store import RuleStore
from argus.salience.budget import InterruptionBudget
from argus.salience.dispatch import SalienceDispatcher
from argus.salience.engine import SalienceEngine
from argus.salience.held import HeldQueue
from argus.salience.scoring import Candidate
from argus.spine.store import SpineStore
from argus.world.model import WorldModel
from argus.world.rhythms import RhythmStore
from argus.world.threads import ThreadStore


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


def _dispatcher(tmp_path, speak_fn=None, cap=3):
    conn = get_connection(tmp_path / "argus.db")
    spine = SpineStore(tmp_path / "spine.db")
    threads = ThreadStore(spine, tmp_path / "argus.db")
    rhythms = RhythmStore(conn)
    world_model = WorldModel(spine=spine, threads=threads, rhythms=rhythms)
    matcher = RuleMatcher(RuleStore(tmp_path / "argus.db"))
    budget = InterruptionBudget(tmp_path / "argus.db")
    held = HeldQueue(tmp_path / "argus.db")
    engine = SalienceEngine(matcher, budget, held)
    lock = threading.Lock()
    dispatcher = SalienceDispatcher(engine, world_model, speak_fn or (lambda text: None), lock)
    return dispatcher, engine


def _boosted_candidate(matcher: RuleMatcher, kind="reminder.due") -> Candidate:
    rule_id = matcher.store.propose(
        natural_language="boost", kind="preference",
        trigger={"kind": kind, "filters": []}, action={"type": "boost", "amount": 1.0},
    )
    matcher.store.confirm(rule_id)
    return Candidate(observation_id=1, kind=kind, subject=None, text="reminder!", base_urgency=1.0)


def test_speak_decision_calls_speak_fn(tmp_path):
    spoken = []
    dispatcher, engine = _dispatcher(tmp_path, speak_fn=lambda text: spoken.append(text))
    candidate = _boosted_candidate(engine.matcher)

    decision = dispatcher.submit(candidate, observation=_FakeObs(kind="reminder.due"), now=1_000_000.0)

    assert decision.action == "speak"
    assert spoken == ["reminder!"]


def test_hold_decision_does_not_call_speak_fn(tmp_path):
    spoken = []
    dispatcher, engine = _dispatcher(tmp_path, speak_fn=lambda text: spoken.append(text))
    candidate = Candidate(observation_id=1, kind="git.branch_stale", subject=None, text="x", base_urgency=0.05)

    decision = dispatcher.submit(candidate, now=1_000_000.0)

    assert decision.action == "hold"
    assert spoken == []


def test_speak_decision_with_busy_lock_is_queued_not_lost(tmp_path):
    spoken = []
    dispatcher, engine = _dispatcher(tmp_path, speak_fn=lambda text: spoken.append(text))
    candidate = _boosted_candidate(engine.matcher)
    dispatcher._interaction_lock.acquire()  # simulate Argus mid-conversation

    decision = dispatcher.submit(candidate, observation=_FakeObs(kind="reminder.due"), now=1_000_000.0)

    assert decision.action == "speak"  # the decision itself is still "speak"
    assert spoken == []  # but nothing was actually said
    pending = engine.held.pending()
    assert len(pending) == 1
    assert pending[0].text == "reminder!"  # nothing silently dropped


def test_decide_exception_is_caught_and_treated_as_hold(tmp_path, monkeypatch):
    spoken = []
    dispatcher, engine = _dispatcher(tmp_path, speak_fn=lambda text: spoken.append(text))
    candidate = Candidate(observation_id=1, kind="reminder.due", subject=None, text="x", base_urgency=0.5)

    def _boom(*a, **k):
        raise RuntimeError("world model exploded")

    monkeypatch.setattr(dispatcher.world_model, "snapshot", _boom)

    decision = dispatcher.submit(candidate)  # must not raise
    assert decision.action == "hold"
    assert spoken == []


def test_now_never_actually_speaks_when_ambient_or_lower(tmp_path):
    spoken = []
    dispatcher, engine = _dispatcher(tmp_path, speak_fn=lambda text: spoken.append(text))
    candidate = Candidate(observation_id=1, kind="routine.due", subject=None, text="x", base_urgency=0.90)

    decision = dispatcher.submit(candidate, now=1_000_000.0)

    assert decision.action == "ambient"
    assert spoken == []


def test_measured_unprompted_interruptions_per_hour_never_exceeds_the_cap(tmp_path, monkeypatch):
    """Phase C acceptance (§5.6 #8), measured directly: flood the
    dispatcher with 50 unambiguous, high-urgency candidates -- every one
    scored "speak" on its own merits -- within a single simulated hour,
    and confirm the number actually spoken never exceeds
    settings.interruptions_per_hour, regardless of how many wanted to."""
    monkeypatch.setattr("argus.salience.budget.settings.interruptions_per_hour", 3)
    spoken = []
    dispatcher, engine = _dispatcher(tmp_path, speak_fn=lambda text: spoken.append(text))
    rule_id = engine.matcher.store.propose(
        natural_language="boost reminders", kind="preference",
        trigger={"kind": "reminder.due", "filters": []}, action={"type": "boost", "amount": 1.0},
    )
    engine.matcher.store.confirm(rule_id)

    base_now = 1_000_000.0
    decisions = []
    for i in range(50):
        candidate = Candidate(observation_id=i, kind="reminder.due", subject=None, text=f"reminder {i}", base_urgency=1.0)
        # Spread across the hour so this also exercises the sliding-window
        # cap, not just "call it 50 times with the same timestamp."
        now = base_now + i * 60
        decisions.append(dispatcher.submit(candidate, observation=_FakeObs(kind="reminder.due"), now=now))

    speak_decisions = [d for d in decisions if d.action == "speak"]
    assert len(speak_decisions) == 3  # the engine itself only ever grants 3
    assert len(spoken) == 3  # and only those 3 were actually said
    # Everything else that would have spoken was held, not lost.
    assert len(engine.held.pending()) == 47
