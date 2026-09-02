"""PRD.md §14 (unit 27): standing authorizations -- the durable, scoped,
revocable sibling of ToolRegistry's in-process `_task_approved` bucket
(§14.1 step 2b). Grants live in the `rules` table as `kind='authorization'`
rows (§14.2), so list_rules/revoke_rule already cover inspection and
revocation; no parallel store.

Reuses Appendix A.3's op vocabulary (rules.matcher.evaluate_ops), applied
to a tool call's arguments instead of an Observation -- see matcher.py's
evaluate_ops docstring for why `fuzzy` isn't shared beyond that: a grant's
`deny` clause must fail toward MATCHING when the judge is unavailable
(blocking auto-approval), the opposite of RuleMatcher's own fuzzy, which
fails a trigger toward no-match. Uncertainty always falls back to asking,
never to acting (§14.3)."""

import time

from argus.rules.matcher import evaluate_ops


def _resolve_field(args: dict, field: str):
    """Dotted path directly into the tool's call arguments -- no
    "payload." prefix, since there's no Observation wrapper here. "self"
    resolves to the whole arguments dict, for a fuzzy clause judging the
    call as a whole rather than one argument."""
    if field == "self":
        return args
    value = args
    for part in field.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


class AuthorizationChecker:
    def __init__(self, rule_store, fuzzy_judge=None):
        """fuzzy_judge: callable(tool_input, value_text) -> bool. Nothing
        wires one up today (mirrors RuleMatcher's own fuzzy_judge, which
        is likewise never passed in production) -- None means every
        fuzzy clause in a grant is unresolved, and §14.3 requires
        unresolved to fail toward asking: no-match for `allow`, match
        for `deny`."""
        self.rule_store = rule_store
        self.fuzzy_judge = fuzzy_judge

    def find_grant(self, tool_name: str, tool_input: dict, now: float | None = None):
        """Returns the granting Rule if an active, unexpired
        authorization covers this call, else None. `deny` beats `allow`,
        always (§14.3) -- checked first, so a matching deny clause can
        never be shadowed by a broader allow."""
        now = now if now is not None else time.time()
        for rule in self.rule_store.list_active():
            if rule.kind != "authorization":
                continue
            grant = rule.authorization or {}
            if grant.get("tool") != tool_name:
                continue
            expires_ts = grant.get("expires_ts")
            if expires_ts is not None and now >= expires_ts:
                continue
            allow = grant.get("allow") or []
            if not allow:
                # §14.3: a grant with an empty allow list is refused at
                # authoring time -- this is a defensive second check
                # against ever honoring one that somehow made it into
                # storage anyway. A blanket "this tool, always" grant is
                # not a scoped authorization.
                continue
            deny = grant.get("deny") or []
            # deny is a safety net: ANY listed pattern matching is enough
            # to block, unlike allow's implicit-AND (Appendix A.3's
            # ordinary filter-list semantics, reused as-is for allow).
            if any(self._matches(tool_input, clause, fuzzy_default=True) for clause in deny):
                continue
            if all(self._matches(tool_input, clause, fuzzy_default=False) for clause in allow):
                return rule
        return None

    def _matches(self, tool_input: dict, clause: dict, fuzzy_default: bool) -> bool:
        op = clause.get("op")
        value = clause.get("value")
        if op == "fuzzy":
            if self.fuzzy_judge is None:
                return fuzzy_default
            return bool(self.fuzzy_judge(tool_input, value))
        field = clause.get("field")
        actual = _resolve_field(tool_input, field) if field else None
        return evaluate_ops(actual, op, value)
