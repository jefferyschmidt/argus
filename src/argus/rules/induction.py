"""PRD.md §7.5 (G4 induction). Daily, over the spine + world model,
looking for patterns worth PROPOSING as a rule -- never activating one
itself. Reuses RuleStore.propose() with origin='induced', going through
the identical confirmation path as a user-authored rule (§7.2's
compiler precedent) -- confirmation is a separate, explicit
RuleStore.confirm() call this module never makes.

This build implements one of the three named heuristics in full --
"repeated dismissals of the same kind" -- plus the shared safety
machinery every induced proposal goes through regardless of which
heuristic eventually finds it: the weekly cap, and never re-proposing a
pattern that's already been suggested (confirmed, still pending, or
rejected). The other two named categories ("repeated identical manual
actions", "consistent timing of when a category gets handled") aren't
built here -- the PRD names the categories but not a concrete detection
algorithm for either, and inventing one risks exactly the kind of
unreviewable "helpful" scope-creep §0 warns against. A future unit can
add them onto the same weekly-cap/dedupe machinery below."""

import logging
import time

from argus.config import settings
from argus.rules.store import RuleStore
from argus.salience.held import HeldQueue

log = logging.getLogger(__name__)

_INDUCTION_WINDOW_SECONDS = 7 * 86400
# At least this many dismissals of one kind in the window before it's
# worth proposing a suppression for -- one or two dismissals is normal
# noise, not a pattern.
_DISMISSAL_THRESHOLD = 3


class InductionEngine:
    def __init__(self, rule_store: RuleStore, held: HeldQueue):
        self.rule_store = rule_store
        self.held = held

    def run_once(self, now: float | None = None) -> list[int]:
        """Looks for inducible patterns and proposes at most
        settings.induced_rule_proposals_per_week rules for them. Returns
        the ids of any newly-proposed rules (each `status='proposed'`,
        `origin='induced'` -- never active)."""
        now = now if now is not None else time.time()
        proposed_ids: list[int] = []

        for kind in self._dismissal_candidates(now):
            if self._weekly_cap_reached(now):
                log.info("G4 induction: weekly proposal cap reached, stopping this run")
                break
            if self._already_proposed_suppression(kind):
                continue
            rule_id = self._propose_suppression(kind)
            proposed_ids.append(rule_id)

        return proposed_ids

    def _dismissal_candidates(self, now: float) -> list[str]:
        since = now - _INDUCTION_WINDOW_SECONDS
        counts: dict[str, int] = {}
        for item in self.held.list_dismissed(since=since):
            counts[item.kind] = counts.get(item.kind, 0) + 1
        return [kind for kind, count in counts.items() if count >= _DISMISSAL_THRESHOLD]

    def _weekly_cap_reached(self, now: float) -> bool:
        since = now - _INDUCTION_WINDOW_SECONDS
        recent = self.rule_store.list_by_origin("induced", since=since)
        return len(recent) >= settings.induced_rule_proposals_per_week

    def _already_proposed_suppression(self, kind: str) -> bool:
        """"A proposal the user rejects is recorded so the same pattern is
        not re-proposed" (§7.5) -- checked against every status, not just
        active/pending: a revoked induced rule is exactly the rejected
        proposal this guard exists for."""
        for rule in self.rule_store.list_by_origin("induced"):
            if rule.trigger.get("kind") == kind and rule.action.get("type") == "suppress":
                return True
        return False

    def _propose_suppression(self, kind: str) -> int:
        return self.rule_store.propose(
            natural_language=f"Stop telling me about {kind} -- you've dismissed it repeatedly.",
            kind="suppression",
            trigger={"kind": kind, "filters": []},
            action={"type": "suppress"},
            origin="induced",
        )
