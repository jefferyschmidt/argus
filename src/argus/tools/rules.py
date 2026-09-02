"""PRD.md §7.6 (G-h introspection): list_rules, explain_last_action,
revoke_rule, activate_mode, deactivate_mode. All close over one shared
RuleStore (and, for explain_last_action, one shared DecisionLog) --
constructing a fresh store per call would still read the same
underlying db file correctly (each store owns its own connection, P1),
but there's no reason to pay for a new connection on every tool call
when one instance is already sitting there."""

import time

from argus.tools.base import PermissionTier, Tool

_STALE_REVIEW_DAYS = 30
_NOISY_FIRES_PER_DAY = 1.0


def _flag(rule, now: float) -> str:
    """Rules that have never fired after 30 days are flagged for review;
    rules firing more than daily are surfaced for confirmation (§7.6)."""
    if rule.status != "active":
        return ""
    age_days = (now - rule.created_ts) / 86400
    if rule.hit_count == 0 and age_days > _STALE_REVIEW_DAYS:
        return " [never fired in 30+ days -- consider revoking]"
    if age_days >= 1 and (rule.hit_count / age_days) > _NOISY_FIRES_PER_DAY:
        return " [fires more than daily -- confirm this is still wanted]"
    return ""


def _describe_rule(rule, now: float) -> str:
    return f"#{rule.id} [{rule.status}] {rule.natural_language}{_flag(rule, now)}"


def _build_list_rules(rule_store) -> Tool:
    def handler(args: dict) -> str:
        status = args.get("status")
        rules = rule_store.list_active() if status == "active" else rule_store.list_pending() if status == "proposed" else None
        if rules is None:
            # No filter: everything worth showing a user -- active and
            # still-pending confirmation, not revoked/disabled clutter.
            rules = rule_store.list_active() + rule_store.list_pending()
        if not rules:
            return "No rules."
        now = time.time()
        return "\n".join(_describe_rule(r, now) for r in rules)

    return Tool(
        name="list_rules",
        description="Lists standing rules (active and pending confirmation by default; pass status='active' or 'proposed' to filter).",
        input_schema={
            "type": "object",
            "properties": {"status": {"type": "string", "description": "Optional filter: 'active' or 'proposed'."}},
        },
        tier=PermissionTier.ALLOW,
        handler=handler,
    )


def _build_explain_last_action(decision_log) -> Tool:
    def handler(args: dict) -> str:
        last = decision_log.last()
        if last is None:
            return "Nothing to explain yet -- no salience decision has been made this run."
        return f"Last action: {last.action} ({last.kind}) -- {last.reason}"

    return Tool(
        name="explain_last_action",
        description="Explains why Argus last spoke, held, suppressed, or stayed quiet about something -- the actual reason that drove it.",
        input_schema={"type": "object", "properties": {}},
        tier=PermissionTier.ALLOW,
        handler=handler,
    )


def _build_revoke_rule(rule_store) -> Tool:
    def handler(args: dict) -> str:
        rule_id = args["rule_id"]
        if rule_store.revoke(rule_id):
            return f"Rule #{rule_id} revoked."
        return f"Rule #{rule_id} couldn't be revoked (not found, or already revoked)."

    return Tool(
        name="revoke_rule",
        description="Revokes a standing rule by id (from list_rules) -- it will never match again.",
        input_schema={
            "type": "object",
            "properties": {"rule_id": {"type": "integer"}},
            "required": ["rule_id"],
        },
        tier=PermissionTier.ALLOW,
        handler=handler,
    )


def _build_activate_mode(rule_store) -> Tool:
    def handler(args: dict) -> str:
        group = args["group"]
        count = rule_store.activate_mode(group)
        if count == 0:
            return f"No disabled rules in mode '{group}' to activate."
        return f"Activated {count} rule(s) in mode '{group}'."

    return Tool(
        name="activate_mode",
        description="Turns on a named mode -- activates every rule sharing this group name, atomically.",
        input_schema={
            "type": "object",
            "properties": {"group": {"type": "string", "description": "The mode's group name, e.g. 'focus'."}},
            "required": ["group"],
        },
        tier=PermissionTier.ALLOW,
        handler=handler,
    )


def _build_deactivate_mode(rule_store) -> Tool:
    def handler(args: dict) -> str:
        group = args["group"]
        count = rule_store.deactivate_mode(group)
        if count == 0:
            return f"No active rules in mode '{group}' to deactivate."
        return f"Deactivated {count} rule(s) in mode '{group}'."

    return Tool(
        name="deactivate_mode",
        description="Turns off a named mode -- deactivates every rule sharing this group name, atomically.",
        input_schema={
            "type": "object",
            "properties": {"group": {"type": "string", "description": "The mode's group name, e.g. 'focus'."}},
            "required": ["group"],
        },
        tier=PermissionTier.ALLOW,
        handler=handler,
    )
