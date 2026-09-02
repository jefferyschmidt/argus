"""PRD.md §14 (unit 27): AuthorizationChecker.find_grant() -- the durable,
scoped, revocable sibling of ToolRegistry's in-process _task_approved
bucket. Tested directly here (no ToolRegistry involved); the gate's own
step-2b wiring is tested in test_registry_authorization_wiring.py."""

import time

from argus.rules.authorization import AuthorizationChecker
from argus.rules.store import RuleStore


def _store(tmp_path) -> RuleStore:
    return RuleStore(tmp_path / "argus.db")


def _grant(store, *, tool="delete_email", allow=None, deny=None, expires_ts=None, status="active"):
    rule_id = store.propose(
        natural_language="test grant", kind="authorization", trigger={}, action={"type": "authorize"},
        authorization={
            "tool": tool,
            "allow": allow if allow is not None else [{"field": "sender", "op": "contains", "value": "newsletter"}],
            "deny": deny if deny is not None else [],
            "expires_ts": expires_ts if expires_ts is not None else time.time() + 86400,
        },
    )
    if status == "active":
        store.confirm(rule_id)
    return rule_id


def test_in_scope_call_is_granted(tmp_path):
    store = _store(tmp_path)
    rule_id = _grant(store)
    checker = AuthorizationChecker(store)

    grant = checker.find_grant("delete_email", {"sender": "newsletter@ex.com"})

    assert grant is not None
    assert grant.id == rule_id


def test_out_of_scope_call_is_not_granted(tmp_path):
    store = _store(tmp_path)
    _grant(store)
    checker = AuthorizationChecker(store)

    assert checker.find_grant("delete_email", {"sender": "boss@work.com"}) is None


def test_wrong_tool_is_not_granted(tmp_path):
    store = _store(tmp_path)
    _grant(store, tool="delete_email")
    checker = AuthorizationChecker(store)

    assert checker.find_grant("send_email", {"sender": "newsletter@ex.com"}) is None


def test_deny_beats_allow(tmp_path):
    """§14.3, non-negotiable: deny clauses beat allow, always."""
    store = _store(tmp_path)
    _grant(
        store,
        allow=[{"field": "sender", "op": "contains", "value": "newsletter"}],
        deny=[{"field": "sender", "op": "contains", "value": "realperson"}],
    )
    checker = AuthorizationChecker(store)

    # Matches allow AND deny -- deny must win.
    assert checker.find_grant("delete_email", {"sender": "newsletter+realperson@ex.com"}) is None
    # Matches allow only -- authorized.
    assert checker.find_grant("delete_email", {"sender": "newsletter@ex.com"}) is not None


def test_empty_allow_list_never_authorizes(tmp_path):
    """§14.3: refused at authoring time, but defended again here too --
    a grant with no allow list must never authorize anything, even if
    one somehow ended up in storage."""
    store = _store(tmp_path)
    _grant(store, allow=[])
    checker = AuthorizationChecker(store)

    assert checker.find_grant("delete_email", {"sender": "anything@ex.com"}) is None


def test_expired_grant_does_not_authorize(tmp_path):
    store = _store(tmp_path)
    _grant(store, expires_ts=time.time() - 1)
    checker = AuthorizationChecker(store)

    assert checker.find_grant("delete_email", {"sender": "newsletter@ex.com"}) is None


def test_unexpired_grant_still_authorizes(tmp_path):
    store = _store(tmp_path)
    _grant(store, expires_ts=time.time() + 3600)
    checker = AuthorizationChecker(store)

    assert checker.find_grant("delete_email", {"sender": "newsletter@ex.com"}) is not None


def test_revoked_grant_does_not_authorize(tmp_path):
    store = _store(tmp_path)
    rule_id = _grant(store)
    store.revoke(rule_id)
    checker = AuthorizationChecker(store)

    assert checker.find_grant("delete_email", {"sender": "newsletter@ex.com"}) is None


def test_fuzzy_allow_clause_with_no_judge_does_not_authorize(tmp_path):
    """§14.3: an unavailable fuzzy judge fails toward asking, never
    toward acting -- no-match for allow."""
    store = _store(tmp_path)
    _grant(store, allow=[{"field": "sender", "op": "fuzzy", "value": "a newsletter"}])
    checker = AuthorizationChecker(store)  # no fuzzy_judge

    assert checker.find_grant("delete_email", {"sender": "newsletter@ex.com"}) is None


def test_fuzzy_deny_clause_with_no_judge_blocks(tmp_path):
    """§14.3: the same fail-toward-asking direction for deny means an
    unresolved fuzzy deny clause MATCHES -- i.e. still blocks -- rather
    than being silently skipped."""
    store = _store(tmp_path)
    _grant(
        store,
        allow=[{"field": "sender", "op": "contains", "value": "newsletter"}],
        deny=[{"field": "sender", "op": "fuzzy", "value": "a real person"}],
    )
    checker = AuthorizationChecker(store)  # no fuzzy_judge

    assert checker.find_grant("delete_email", {"sender": "newsletter@ex.com"}) is None


def test_fuzzy_clauses_resolve_normally_when_a_judge_is_available(tmp_path):
    store = _store(tmp_path)
    _grant(store, allow=[{"field": "sender", "op": "fuzzy", "value": "a newsletter"}])
    checker = AuthorizationChecker(store, fuzzy_judge=lambda tool_input, value: "newsletter" in tool_input.get("sender", ""))

    assert checker.find_grant("delete_email", {"sender": "newsletter@ex.com"}) is not None
    assert checker.find_grant("delete_email", {"sender": "boss@work.com"}) is None


def test_only_authorization_kind_rules_are_considered(tmp_path):
    """An ordinary (non-authorization) active rule sharing no shape with
    a grant must never be mistaken for one."""
    store = _store(tmp_path)
    rule_id = store.propose(
        natural_language="suppress mail", kind="suppression",
        trigger={"kind": "mail.received", "filters": []}, action={"type": "suppress"},
    )
    store.confirm(rule_id)
    checker = AuthorizationChecker(store)

    assert checker.find_grant("delete_email", {"sender": "newsletter@ex.com"}) is None


def test_pending_proposed_grant_does_not_authorize(tmp_path):
    """Only confirmed (status='active') grants authorize -- a merely
    proposed one must not."""
    store = _store(tmp_path)
    _grant(store, status="proposed")
    checker = AuthorizationChecker(store)

    assert checker.find_grant("delete_email", {"sender": "newsletter@ex.com"}) is None
