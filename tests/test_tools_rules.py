import time
from unittest.mock import MagicMock

from argus.rules.store import Rule
from argus.salience.decision_log import LoggedDecision
from argus.tools.base import PermissionTier
from argus.tools.rules import (
    _build_activate_mode,
    _build_deactivate_mode,
    _build_explain_last_action,
    _build_list_rules,
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
