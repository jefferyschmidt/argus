import json
import time
from unittest.mock import MagicMock

from argus.llm.base import CompletionResult, Tier
from argus.rules.store import Rule, RuleStore
from argus.salience.decision_log import LoggedDecision
from argus.tools.base import PermissionTier
from argus.tools.rules import (
    _build_activate_mode,
    _build_deactivate_mode,
    _build_explain_last_action,
    _build_list_rules,
    _build_remember_preference,
    _build_revoke_rule,
)


def _rule(**overrides) -> Rule:
    defaults = dict(
        id=1, natural_language="Suppress newsletters", source_utterance=None, kind="suppression",
        trigger={"kind": "mail.received", "filters": []}, conditions=[], action={"type": "suppress"},
        until_condition=None, group_name=None, status="active", authorization=None,
        created_ts=time.time(), confirmed_ts=time.time(), revoked_ts=None,
        hit_count=0, last_fired_ts=None, origin="user",
    )
    defaults.update(overrides)
    return Rule(**defaults)


# -- tiers --------------------------------------------------------------

def test_all_introspection_tools_are_allow_tier():
    store = MagicMock()
    log = MagicMock()
    assert _build_list_rules(store).tier == PermissionTier.ALLOW
    assert _build_revoke_rule(store).tier == PermissionTier.ALLOW
    assert _build_activate_mode(store).tier == PermissionTier.ALLOW
    assert _build_deactivate_mode(store).tier == PermissionTier.ALLOW
    assert _build_explain_last_action(log).tier == PermissionTier.ALLOW


# -- list_rules ---------------------------------------------------------

def test_list_rules_with_no_rules():
    store = MagicMock()
    store.list_active.return_value = []
    store.list_pending.return_value = []
    tool = _build_list_rules(store)

    assert tool.handler({}) == "No rules."


def test_list_rules_shows_active_and_pending_by_default():
    store = MagicMock()
    store.list_active.return_value = [_rule(id=1, status="active")]
    store.list_pending.return_value = [_rule(id=2, status="proposed")]
    tool = _build_list_rules(store)

    result = tool.handler({})
    assert "#1" in result
    assert "#2" in result


def test_list_rules_filters_by_status():
    store = MagicMock()
    store.list_active.return_value = [_rule(id=1)]
    tool = _build_list_rules(store)

    tool.handler({"status": "active"})
    store.list_active.assert_called_once()
    store.list_pending.assert_not_called()


def test_list_rules_flags_a_never_fired_stale_rule():
    store = MagicMock()
    old_unfired = _rule(id=1, hit_count=0, created_ts=time.time() - 40 * 86400)
    store.list_active.return_value = [old_unfired]
    store.list_pending.return_value = []
    tool = _build_list_rules(store)

    result = tool.handler({})
    assert "never fired" in result


def test_list_rules_flags_a_noisy_rule():
    store = MagicMock()
    noisy = _rule(id=1, hit_count=50, created_ts=time.time() - 10 * 86400)  # 5/day
    store.list_active.return_value = [noisy]
    store.list_pending.return_value = []
    tool = _build_list_rules(store)

    result = tool.handler({})
    assert "fires more than daily" in result


def test_list_rules_does_not_flag_a_healthy_rule():
    store = MagicMock()
    healthy = _rule(id=1, hit_count=3, created_ts=time.time() - 10 * 86400)
    store.list_active.return_value = [healthy]
    store.list_pending.return_value = []
    tool = _build_list_rules(store)

    result = tool.handler({})
    assert "never fired" not in result
    assert "fires more than daily" not in result


# -- explain_last_action -----------------------------------------------

def test_explain_last_action_with_no_history():
    log = MagicMock()
    log.last.return_value = None
    tool = _build_explain_last_action(log)

    assert "Nothing to explain" in tool.handler({})


def test_explain_last_action_returns_the_decisions_reason():
    log = MagicMock()
    log.last.return_value = LoggedDecision(
        id=1, ts=time.time(), kind="mail.received", subject="a@x.com",
        action="hold", reason="score 0.19 below ambient threshold 0.3",
    )
    tool = _build_explain_last_action(log)

    result = tool.handler({})
    assert "hold" in result
    assert "score 0.19 below ambient threshold 0.3" in result


# -- revoke_rule ----------------------------------------------------------

def test_revoke_rule_success():
    store = MagicMock()
    store.revoke.return_value = True
    tool = _build_revoke_rule(store)

    result = tool.handler({"rule_id": 1})
    store.revoke.assert_called_once_with(1)
    assert "revoked" in result


def test_revoke_rule_failure():
    store = MagicMock()
    store.revoke.return_value = False
    tool = _build_revoke_rule(store)

    result = tool.handler({"rule_id": 1})
    assert "couldn't be revoked" in result


# -- activate_mode / deactivate_mode ----------------------------------------

def test_activate_mode_reports_count():
    store = MagicMock()
    store.activate_mode.return_value = 3
    tool = _build_activate_mode(store)

    result = tool.handler({"group": "focus"})
    store.activate_mode.assert_called_once_with("focus")
    assert "3" in result
    assert "focus" in result


def test_activate_mode_with_nothing_to_activate():
    store = MagicMock()
    store.activate_mode.return_value = 0
    tool = _build_activate_mode(store)

    result = tool.handler({"group": "focus"})
    assert "No disabled rules" in result


def test_deactivate_mode_reports_count():
    store = MagicMock()
    store.deactivate_mode.return_value = 2
    tool = _build_deactivate_mode(store)

    result = tool.handler({"group": "focus"})
    store.deactivate_mode.assert_called_once_with("focus")
    assert "2" in result


# -- remember_preference (PRD §13 unit 25) ------------------------------

def _router(reply_text: str) -> MagicMock:
    router = MagicMock()
    router.complete.return_value = CompletionResult(text=reply_text, tier=Tier.ADVANCED, model="test")
    return router


_SUPPRESS_REPLY = json.dumps({
    "natural_language": "Don't tell me when I open Claude",
    "kind": "suppression",
    "trigger": {"kind": "app.focus_changed", "filters": [{"field": "payload.app", "op": "eq", "value": "Claude"}]},
    "action": {"type": "suppress"},
    "conditions": [], "until_condition": None, "group_name": None,
})


def test_remember_preference_is_confirm_tier():
    store = RuleStore(":memory:")
    assert _build_remember_preference(store, _router(_SUPPRESS_REPLY)).tier == PermissionTier.CONFIRM


def test_remember_preference_activates_a_rule_that_survives_a_restart(tmp_path):
    db_path = tmp_path / "argus.db"
    store = RuleStore(db_path)
    tool = _build_remember_preference(store, _router(_SUPPRESS_REPLY))

    result = tool.handler({"utterance": "stop telling me when I open Claude"})

    assert "Don't tell me when I open Claude" in result
    assert "now active" in result
    reopened = RuleStore(db_path)
    [rule] = reopened.list_active()
    assert rule.natural_language == "Don't tell me when I open Claude"
    assert rule.status == "active"
    assert rule.origin == "user"


def test_remember_preference_surfaces_the_clarifying_question_and_proposes_nothing():
    store = RuleStore(":memory:")
    reply = json.dumps({"clarifying_question": "Stop telling you about which app, exactly?"})
    tool = _build_remember_preference(store, _router(reply))

    result = tool.handler({"utterance": "stop telling me about that"})

    assert result == "Stop telling you about which app, exactly?"
    assert store.list_active() == []
    assert store.list_pending() == []


def test_remember_preference_reports_a_conflict_with_an_existing_active_rule():
    store = RuleStore(":memory:")
    existing_id = store.propose(
        natural_language="Suppress everything from Claude", kind="suppression",
        trigger={"kind": "app.focus_changed", "filters": []}, action={"type": "suppress"},
    )
    store.confirm(existing_id)
    tool = _build_remember_preference(store, _router(_SUPPRESS_REPLY))

    result = tool.handler({"utterance": "stop telling me when I open Claude"})

    assert f"#{existing_id}" in result
    assert "conflicts" in result


def test_a_rule_authored_this_way_actually_suppresses():
    """Unit 25 acceptance: the matching candidate returns
    Decision(action='suppress') from SalienceEngine -- not just that a row
    landed in the rules table."""
    from argus.rules.matcher import RuleMatcher
    from argus.salience.budget import InterruptionBudget
    from argus.salience.engine import SalienceEngine
    from argus.salience.held import HeldQueue
    from argus.salience.scoring import Candidate
    from argus.world.model import RhythmSummary, WorldSnapshot
    from dataclasses import dataclass
    from datetime import datetime

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

    store = RuleStore(":memory:")
    tool = _build_remember_preference(store, _router(_SUPPRESS_REPLY))
    tool.handler({"utterance": "stop telling me when I open Claude"})

    matcher = RuleMatcher(store)
    engine = SalienceEngine(matcher, InterruptionBudget(":memory:"), HeldQueue(":memory:"))
    snapshot = WorldSnapshot(now=datetime.now(), focus=None, open_threads=[], horizon=[], devices={}, health=[], rhythms=RhythmSummary())
    candidate = Candidate(observation_id=1, kind="app.focus_changed", subject=None, text="x", base_urgency=1.0)
    observation = _FakeObs(kind="app.focus_changed", payload={"app": "Claude"})

    decision = engine.decide(candidate, snapshot, observation=observation, now=1_000_000.0)

    assert decision.action == "suppress"
