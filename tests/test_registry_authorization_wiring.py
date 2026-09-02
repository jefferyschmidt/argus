"""PRD.md §14.1/§14.5 (unit 27): step 2b in ToolRegistry.execute() --
checked after the in-process _task_approved bucket and before the
confirmer, changing nothing else about the existing gate."""

import time
from unittest.mock import MagicMock

from argus.rules.authorization import AuthorizationChecker
from argus.rules.store import RuleStore
from argus.spine.observation import Observation
from argus.spine.store import SpineStore
from argus.tools.base import PermissionTier, Tool
from argus.tools.registry import ToolRegistry


def _tool(name="delete_email", tier=PermissionTier.CONFIRM, **overrides) -> Tool:
    return Tool(
        name=name, description="test", input_schema={"type": "object", "properties": {}},
        tier=tier, handler=lambda args: f"ran {name}", **overrides,
    )


def _rule_store(tmp_path) -> RuleStore:
    return RuleStore(tmp_path / "argus.db")


def _grant(store, *, tool="delete_email", allow=None, deny=None, expires_ts=None):
    rule_id = store.propose(
        natural_language="test grant", kind="authorization", trigger={}, action={"type": "authorize"},
        authorization={
            "tool": tool,
            "allow": allow if allow is not None else [{"field": "sender", "op": "contains", "value": "newsletter"}],
            "deny": deny if deny is not None else [],
            "expires_ts": expires_ts if expires_ts is not None else time.time() + 86400,
        },
    )
    store.confirm(rule_id)
    return rule_id


def test_granted_in_scope_call_runs_with_no_confirmer_invocation(tmp_path):
    store = _rule_store(tmp_path)
    _grant(store)
    confirmer = MagicMock(return_value=True)
    registry = ToolRegistry(confirmer=confirmer, authorization_checker=AuthorizationChecker(store))
    registry.register(_tool())

    result = registry.execute("delete_email", {"sender": "newsletter@ex.com"})

    assert result == "ran delete_email"
    confirmer.assert_not_called()


def test_out_of_scope_call_still_prompts_normally(tmp_path):
    store = _rule_store(tmp_path)
    _grant(store)
    confirmer = MagicMock(return_value=True)
    registry = ToolRegistry(confirmer=confirmer, authorization_checker=AuthorizationChecker(store))
    registry.register(_tool())

    result = registry.execute("delete_email", {"sender": "boss@work.com"})

    assert result == "ran delete_email"
    confirmer.assert_called_once_with("delete_email", {"sender": "boss@work.com"})


def test_deny_beats_allow_at_the_gate(tmp_path):
    store = _rule_store(tmp_path)
    _grant(
        store,
        allow=[{"field": "sender", "op": "contains", "value": "newsletter"}],
        deny=[{"field": "sender", "op": "contains", "value": "realperson"}],
    )
    confirmer = MagicMock(return_value=True)
    registry = ToolRegistry(confirmer=confirmer, authorization_checker=AuthorizationChecker(store))
    registry.register(_tool())

    registry.execute("delete_email", {"sender": "newsletter+realperson@ex.com"})

    confirmer.assert_called_once()  # fell through to the confirmer, not auto-approved


def test_expired_grant_does_not_authorize_at_the_gate(tmp_path):
    store = _rule_store(tmp_path)
    _grant(store, expires_ts=time.time() - 1)
    confirmer = MagicMock(return_value=True)
    registry = ToolRegistry(confirmer=confirmer, authorization_checker=AuthorizationChecker(store))
    registry.register(_tool())

    registry.execute("delete_email", {"sender": "newsletter@ex.com"})

    confirmer.assert_called_once()


def test_deny_tier_tool_is_never_run_via_a_grant(tmp_path):
    """§14.3, non-negotiable: a grant never covers PermissionTier.DENY --
    that tier means disabled, not 'ask first'. Structurally guaranteed:
    DENY returns before the CONFIRM branch (and step 2b within it) is
    ever reached, regardless of what any grant says."""
    store = _rule_store(tmp_path)
    _grant(store, tool="dangerous_tool", allow=[{"field": "self", "op": "eq", "value": {}}])
    confirmer = MagicMock(return_value=True)
    registry = ToolRegistry(confirmer=confirmer, authorization_checker=AuthorizationChecker(store))
    registry.register(_tool(name="dangerous_tool", tier=PermissionTier.DENY))

    result = registry.execute("dangerous_tool", {})

    assert "deny tier" in result
    confirmer.assert_not_called()


def test_every_auto_approved_call_writes_exactly_one_tool_auto_approved_observation(tmp_path):
    store = _rule_store(tmp_path)
    rule_id = _grant(store)
    spine = SpineStore(tmp_path / "spine.db")
    registry = ToolRegistry(
        confirmer=MagicMock(return_value=True),
        authorization_checker=AuthorizationChecker(store), spine=spine,
    )
    registry.register(_tool())

    registry.execute("delete_email", {"sender": "newsletter@ex.com"})

    observations = spine.query(kinds=["tool.auto_approved"])
    assert len(observations) == 1
    obs = observations[0]
    assert obs.payload["tool"] == "delete_email"
    assert obs.payload["arguments"] == {"sender": "newsletter@ex.com"}
    assert obs.payload["rule_id"] == rule_id


def test_no_observation_written_when_the_call_was_not_auto_approved(tmp_path):
    store = _rule_store(tmp_path)
    _grant(store)
    spine = SpineStore(tmp_path / "spine.db")
    registry = ToolRegistry(
        confirmer=MagicMock(return_value=True),
        authorization_checker=AuthorizationChecker(store), spine=spine,
    )
    registry.register(_tool())

    registry.execute("delete_email", {"sender": "boss@work.com"})  # out of scope -> confirmer path

    assert spine.query(kinds=["tool.auto_approved"]) == []


def test_no_spine_wired_does_not_break_auto_approval(tmp_path):
    """§1: optional collaborators fail soft -- no spine means the
    observation is simply never written, not an error."""
    store = _rule_store(tmp_path)
    _grant(store)
    registry = ToolRegistry(confirmer=MagicMock(return_value=True), authorization_checker=AuthorizationChecker(store))
    registry.register(_tool())

    result = registry.execute("delete_email", {"sender": "newsletter@ex.com"})

    assert result == "ran delete_email"


def test_revoke_rule_restores_normal_prompting_immediately(tmp_path):
    store = _rule_store(tmp_path)
    rule_id = _grant(store)
    confirmer = MagicMock(return_value=True)
    registry = ToolRegistry(confirmer=confirmer, authorization_checker=AuthorizationChecker(store))
    registry.register(_tool())

    store.revoke(rule_id)
    registry.execute("delete_email", {"sender": "newsletter@ex.com"})

    confirmer.assert_called_once()


def test_grants_survive_a_restart(tmp_path):
    db_path = tmp_path / "argus.db"
    store = RuleStore(db_path)
    _grant(store)

    reopened = RuleStore(db_path)
    confirmer = MagicMock(return_value=True)
    registry = ToolRegistry(confirmer=confirmer, authorization_checker=AuthorizationChecker(reopened))
    registry.register(_tool())

    registry.execute("delete_email", {"sender": "newsletter@ex.com"})

    confirmer.assert_not_called()


def test_no_authorization_checker_wired_falls_through_to_the_confirmer(tmp_path):
    """Default behavior (authorization_checker=None) is completely
    unaffected -- step 2b is skipped, not errored on."""
    confirmer = MagicMock(return_value=True)
    registry = ToolRegistry(confirmer=confirmer)
    registry.register(_tool())

    registry.execute("delete_email", {"sender": "newsletter@ex.com"})

    confirmer.assert_called_once()


def test_step_2b_does_not_disturb_explicit_task_authorization(tmp_path):
    """§14.1: step 1 (_explicit_task_authorized) still short-circuits
    before step 2b is ever consulted."""
    store = _rule_store(tmp_path)
    # Deliberately no grant at all -- if step 1 didn't still work, this
    # call would fall through to the confirmer instead of running.
    confirmer = MagicMock(return_value=True)
    registry = ToolRegistry(confirmer=confirmer, authorization_checker=AuthorizationChecker(store))
    registry.register(_tool())
    registry.reset_task_autonomy(explicitly_requested=True)

    result = registry.execute("delete_email", {"sender": "boss@work.com"})

    assert result == "ran delete_email"
    confirmer.assert_not_called()


def test_step_2b_does_not_disturb_task_approved_bucket(tmp_path):
    """§14.1: step 2 (_task_approved, session-scoped) still wins before
    step 2b is ever consulted."""
    store = _rule_store(tmp_path)
    confirmer = MagicMock(return_value=True)
    registry = ToolRegistry(confirmer=confirmer, authorization_checker=AuthorizationChecker(store))
    registry.register(_tool(repeatable=True))

    registry.execute("delete_email", {"sender": "boss@work.com"})  # first call -- confirms, records approval
    registry.execute("delete_email", {"sender": "someone-else@work.com"})  # second call -- task-approved bucket

    confirmer.assert_called_once()


def test_step_2b_does_not_disturb_high_risk_double_confirm(tmp_path):
    """§14.1: high_risk's own double confirmation (steps 3+4) is
    untouched when no grant applies."""
    store = _rule_store(tmp_path)
    confirmer = MagicMock(return_value=True)
    registry = ToolRegistry(confirmer=confirmer, authorization_checker=AuthorizationChecker(store))
    registry.register(_tool(high_risk=True))

    registry.execute("delete_email", {"sender": "boss@work.com"})

    assert confirmer.call_count == 2


def test_grant_with_a_dedicated_observation_records_the_correct_source_and_kind(tmp_path):
    store = _rule_store(tmp_path)
    _grant(store)
    spine = SpineStore(tmp_path / "spine.db")
    registry = ToolRegistry(
        confirmer=MagicMock(return_value=True),
        authorization_checker=AuthorizationChecker(store), spine=spine,
    )
    registry.register(_tool())

    registry.execute("delete_email", {"sender": "newsletter@ex.com"})

    (obs,) = spine.query(kinds=["tool.auto_approved"])
    assert isinstance(obs, Observation)
    assert obs.kind == "tool.auto_approved"


def test_a_granted_call_does_not_widen_the_grant_for_the_rest_of_the_turn(tmp_path):
    """The scope-leak path worth guarding explicitly. `delete_email` is
    repeatable, so a normal confirmed call adds its approval_key to
    _task_approved and every later call that turn runs unprompted. If an
    auto-approved call did the same, one in-scope newsletter delete would
    silently authorize deleting anything -- including from a real person,
    which is exactly what the grant's deny clause exists to prevent. Step
    2b therefore returns without touching the bucket, unlike the
    _explicit_task_authorized path directly above it.

    Sequenced deliberately: in-scope call first, out-of-scope call second,
    same turn, no reset_task_autonomy() in between."""
    store = _rule_store(tmp_path)
    _grant(store, deny=[{"field": "sender", "op": "contains", "value": "@person"}])
    confirmer = MagicMock(return_value=True)
    registry = ToolRegistry(confirmer=confirmer, authorization_checker=AuthorizationChecker(store))
    registry.register(_tool(repeatable=True))

    registry.execute("delete_email", {"sender": "newsletter@ex.com"})
    confirmer.assert_not_called()

    registry.execute("delete_email", {"sender": "julia@person.com"})

    assert confirmer.call_count == 1, (
        "an auto-approved call widened the session approval bucket -- the second, "
        "out-of-scope call ran without asking"
    )
