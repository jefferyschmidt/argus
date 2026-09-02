from unittest.mock import MagicMock

from argus.tools import build_default_registry


def test_rule_tools_absent_without_a_rule_store():
    registry = build_default_registry(router=None, rule_store=None)
    assert "list_rules" not in registry._tools
    assert "revoke_rule" not in registry._tools
    assert "activate_mode" not in registry._tools
    assert "deactivate_mode" not in registry._tools


def test_rule_tools_present_with_a_rule_store():
    registry = build_default_registry(router=None, rule_store=MagicMock())
    assert "list_rules" in registry._tools
    assert "revoke_rule" in registry._tools
    assert "activate_mode" in registry._tools
    assert "deactivate_mode" in registry._tools


def test_remember_preference_absent_without_a_router_even_with_a_rule_store():
    """RuleCompiler (unit 15) needs a live router for its authoring-time
    LLM call -- rule_store alone isn't enough, unlike the other rule tools
    which only read/mutate the store."""
    registry = build_default_registry(router=None, rule_store=MagicMock())
    assert "remember_preference" not in registry._tools


def test_remember_preference_absent_without_a_rule_store_even_with_a_router():
    registry = build_default_registry(router=MagicMock(), rule_store=None)
    assert "remember_preference" not in registry._tools


def test_remember_preference_present_with_both_a_router_and_a_rule_store():
    registry = build_default_registry(router=MagicMock(), rule_store=MagicMock())
    assert "remember_preference" in registry._tools


def test_declining_the_confirmation_leaves_no_rule_at_all(tmp_path):
    """Unit 25 acceptance: declining the confirmation leaves no active
    rule. Since remember_preference is CONFIRM-tier, a decline is caught
    by ToolRegistry itself before the handler (compile+propose+confirm)
    ever runs -- so nothing is left behind, not even a 'proposed' row."""
    from argus.rules.store import RuleStore
    from argus.tools.registry import ToolDenied, ToolRegistry
    from argus.tools.rules import _build_remember_preference

    store = RuleStore(tmp_path / "argus.db")
    router = MagicMock()
    registry = ToolRegistry(confirmer=lambda name, tool_input: False)
    registry.register(_build_remember_preference(store, router))

    try:
        registry.execute("remember_preference", {"utterance": "stop telling me when I open Claude"})
        raised = False
    except ToolDenied:
        raised = True

    assert raised
    router.complete.assert_not_called()
    assert store.list_active() == []
    assert store.list_pending() == []


def test_explain_last_action_absent_without_a_decision_log():
    registry = build_default_registry(router=None, decision_log=None)
    assert "explain_last_action" not in registry._tools


def test_explain_last_action_present_with_a_decision_log():
    registry = build_default_registry(router=None, decision_log=MagicMock())
    assert "explain_last_action" in registry._tools
