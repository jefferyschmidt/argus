import logging
import time
from typing import Callable

from argus.spine.observation import Observation
from argus.tools.base import PermissionTier, Tool

log = logging.getLogger(__name__)

# Confirmer takes (tool_name, tool_input) and returns True/False. Swappable so
# voice mode can confirm by speech instead of console input later.
Confirmer = Callable[[str, dict], bool]


def console_confirmer(tool_name: str, tool_input: dict) -> bool:
    print(f"\n[confirm] Argus wants to run '{tool_name}' with input: {tool_input}")
    answer = input("Allow? [y/N] ").strip().lower()
    # A strict answer == "y" silently treated "yes" -- a completely
    # natural thing to type -- as a decline, with no error or feedback.
    return answer in ("y", "yes", "yeah", "yep", "sure", "ok", "okay")


class ToolDenied(Exception):
    pass


class ToolRegistry:
    def __init__(self, confirmer: Confirmer = console_confirmer, authorization_checker=None, spine=None):
        self._tools: dict[str, Tool] = {}
        self.confirmer = confirmer
        # PRD §14 (unit 27): the durable, scoped, revocable sibling of
        # _task_approved below. None (the default) means no standing
        # authorizations exist yet -- step 2b in execute() is skipped
        # entirely rather than querying a store that was never wired.
        self.authorization_checker = authorization_checker
        # Only used to record tool.auto_approved (§14.3) when a grant
        # fires -- None means that observation is simply never written,
        # same "optional collaborator, fails soft" pattern as everywhere
        # else in this codebase (§1).
        self.spine = spine
        self._task_approved: set[str] = set()
        self._explicit_task_authorized = False

    def reset_task_autonomy(self, explicitly_requested: bool = False) -> None:
        """Call at the start of each new user-initiated turn -- approval for
        a repeatable tool only carries across the rest of ONE task, not the
        whole session."""
        self._task_approved.clear()
        # A user who plainly asked Argus to perform an action has already
        # made the relevant choice. Re-asking for every controlled step is
        # friction, not protection. Calls that arise without an explicit
        # action request retain the normal confirmation gate.
        self._explicit_task_authorized = explicitly_requested

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def schemas(self) -> list[dict]:
        return [t.to_anthropic_schema() for t in self._tools.values() if t.tier != PermissionTier.DENY]

    def execute(self, name: str, tool_input: dict) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return f"error: unknown tool '{name}'"

        if tool.tier is PermissionTier.DENY:
            return f"error: tool '{name}' is registered but disabled (deny tier)"

        if tool.tier is PermissionTier.CONFIRM:
            if self._explicit_task_authorized:
                log.info("Executing tool: %s(%s) [tier=%s, explicitly requested by user]", name, tool_input, tool.tier.value)
                if getattr(tool, "repeatable", False):
                    self._task_approved.add(getattr(tool, "group", None) or name)
                return tool.handler(tool_input)
            # Tools sharing a `group` share one approval bucket -- e.g. all
            # of desktop control, so "open my calculator and add 4+4"
            # (open_app, then several clicks) asks once total, not once
            # per distinct tool name. Ungrouped repeatable tools fall back
            # to their own name as the bucket key, same as before groups
            # existed.
            approval_key = getattr(tool, "group", None) or name
            if getattr(tool, "repeatable", False) and approval_key in self._task_approved:
                log.info("Executing tool: %s(%s) [tier=%s, auto-approved for this task]", name, tool_input, tool.tier.value)
                return tool.handler(tool_input)
            # Step 2b (PRD §14.1, unit 27): a standing authorization grant
            # -- durable and scoped, unlike _task_approved above (in-
            # process, cleared every turn). Checked after that bucket and
            # before the confirmer; changes nothing else about this gate.
            # A grant can never cover a DENY-tier tool -- moot here, DENY
            # already returned above before this branch is ever reached.
            if self.authorization_checker is not None:
                grant = self.authorization_checker.find_grant(name, tool_input)
                if grant is not None:
                    log.info(
                        "Executing tool: %s(%s) [tier=%s, auto-approved by grant #%s]",
                        name, tool_input, tool.tier.value, grant.id,
                    )
                    self._record_auto_approved(name, tool_input, grant.id)
                    return tool.handler(tool_input)
            if not self.confirmer(name, tool_input):
                log.info("Tool call denied by user: %s(%s)", name, tool_input)
                raise ToolDenied(f"user declined to run '{name}'")
            # high_risk tools (send_email, restart_argus, commit_own_changes,
            # write_own_source) get asked twice, not once -- a single
            # misheard "yes" is a real risk, and these are the actions
            # where that's most costly. Both confirmations must pass.
            if getattr(tool, "high_risk", False) and not self.confirmer(name, tool_input):
                log.info("Tool call denied by user on second confirmation: %s(%s)", name, tool_input)
                raise ToolDenied(f"user declined to run '{name}' on second confirmation")
            if getattr(tool, "repeatable", False):
                self._task_approved.add(approval_key)

        log.info("Executing tool: %s(%s) [tier=%s]", name, tool_input, tool.tier.value)
        return tool.handler(tool_input)

    def _record_auto_approved(self, name: str, tool_input: dict, rule_id: int) -> None:
        """PRD §14.3: the only record of what Argus did without asking --
        without this, tool.auto_approved calls leave no audit trail at
        all. Required, not optional; the spine argument being None (no
        collaborator wired) is the only thing that skips it."""
        if self.spine is None:
            return
        self.spine.record(Observation(
            source="tools", kind="tool.auto_approved", ts=time.time(), subject=name,
            payload={"tool": name, "arguments": tool_input, "rule_id": rule_id},
        ))
