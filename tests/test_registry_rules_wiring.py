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


def test_explain_last_action_absent_without_a_decision_log():
    registry = build_default_registry(router=None, decision_log=None)
    assert "explain_last_action" not in registry._tools


def test_explain_last_action_present_with_a_decision_log():
    registry = build_default_registry(router=None, decision_log=MagicMock())
    assert "explain_last_action" in registry._tools
