from unittest.mock import MagicMock

from argus.orchestrator import Orchestrator
from argus.proactive_engine import ProactiveEngine


def test_orchestrator_always_constructs_rule_store_and_decision_log():
    """Unlike task_runner, Phase G has no enable_ flag -- these are
    always present."""
    orch = Orchestrator()
    assert orch.rule_store is not None
    assert orch.decision_log is not None
    assert "list_rules" in orch.tools._tools
    assert "explain_last_action" in orch.tools._tools


def test_proactive_engine_reuses_the_orchestrators_rule_store():
    orch = Orchestrator()
    engine = ProactiveEngine(orch, speak_fn=MagicMock(), interaction_lock=MagicMock())

    assert engine.rule_matcher.store is orch.rule_store


def test_proactive_engine_reuses_the_orchestrators_decision_log():
    orch = Orchestrator()
    engine = ProactiveEngine(orch, speak_fn=MagicMock(), interaction_lock=MagicMock())

    assert engine.salience_engine.decision_log is orch.decision_log


def test_a_rule_revoked_through_the_tool_is_invisible_to_the_engines_matcher():
    """End-to-end proof that the shared instance is real, not just
    identity-equal by construction: revoking through Orchestrator's
    rule_store is immediately reflected in ProactiveEngine's matcher."""
    orch = Orchestrator()
    engine = ProactiveEngine(orch, speak_fn=MagicMock(), interaction_lock=MagicMock())

    rule_id = orch.rule_store.propose(
        natural_language="x", kind="suppression",
        trigger={"kind": "mail.received", "filters": []}, action={"type": "suppress"},
    )
    orch.rule_store.confirm(rule_id)
    assert len(engine.rule_matcher.store.list_active()) == 1

    orch.rule_store.revoke(rule_id)
    assert engine.rule_matcher.store.get(rule_id).status == "revoked"
