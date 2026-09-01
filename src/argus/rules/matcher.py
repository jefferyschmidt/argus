"""PRD.md §7.3. Deterministic, indexed by trigger.kind. No LLM. This is
the base version: trigger.kind matching with an empty (or absent)
filters list matching every observation of that kind, per Appendix A.3
("An empty list matches every observation of that kind"). The full
filter-op vocabulary and the fuzzy verdict cache are unit 15a
(Appendix A.3) -- extended in place below by that unit."""

import logging

from argus.rules.store import RuleStore

log = logging.getLogger(__name__)


class RuleMatcher:
    def __init__(self, store: RuleStore):
        self.store = store

    def match(self, obs) -> list:
        """Every active rule whose trigger.kind equals obs.kind and whose
        filters (if any) all pass. Rules are indexed by trigger.kind at
        query time via RuleStore.list_active(); with an empty filters
        list every rule of that kind matches."""
        candidates = self.store.list_active()
        matched = []
        for rule in candidates:
            if rule.trigger.get("kind") != obs.kind:
                continue
            filters = rule.trigger.get("filters") or []
            if not filters:
                matched.append(rule)
        return matched
