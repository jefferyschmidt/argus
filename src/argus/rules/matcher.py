"""PRD.md §7.3 + Appendix A.3. Deterministic, indexed by trigger.kind. No
LLM -- except the one permitted op, `fuzzy`, which delegates to a caller-
supplied judge (the existing triage call, e.g. email_watcher's importance
classifier) through a shared per-observation verdict cache, so N rules
asking "is this important?" about the same observation cost one call, not
N (Appendix A.3)."""

import logging
import re
import time

from argus.rules.store import RuleStore

log = logging.getLogger(__name__)

# Appendix A.3: cache key (observation_id, value); a cache MISS for an
# observation older than this evaluates to False rather than issuing a
# fresh call, so a backlog replay can't trigger a burst of LLM calls.
_FUZZY_CACHE_TTL_SECONDS = 3600

_COMPARISON_OPS = {
    "eq": lambda a, b: a == b,
    "neq": lambda a, b: a != b,
    "contains": lambda a, b: isinstance(a, str) and isinstance(b, str) and b in a,
    "not_contains": lambda a, b: not (isinstance(a, str) and isinstance(b, str) and b in a),
    "gt": lambda a, b: a is not None and b is not None and a > b,
    "gte": lambda a, b: a is not None and b is not None and a >= b,
    "lt": lambda a, b: a is not None and b is not None and a < b,
    "lte": lambda a, b: a is not None and b is not None and a <= b,
    "in": lambda a, b: isinstance(b, list) and a in b,
}

_regex_cache: dict[str, "re.Pattern | None"] = {}


def _compiled_regex(pattern: str) -> "re.Pattern | None":
    if pattern not in _regex_cache:
        try:
            _regex_cache[pattern] = re.compile(pattern)
        except re.error:
            log.warning("Invalid regex in rule filter: %r", pattern)
            _regex_cache[pattern] = None
    return _regex_cache[pattern]


def _resolve_field(obs, field: str):
    """Dotted path against an Observation: subject, source, confidence,
    or payload.<key> (nested allowed). Unresolvable is no-match (None).
    "self" resolves to the observation itself, for ops (fuzzy) that judge
    the whole thing rather than one field."""
    if field == "self":
        return obs
    parts = field.split(".")
    if parts[0] == "payload":
        value = obs.payload
        for part in parts[1:]:
            if not isinstance(value, dict) or part not in value:
                return None
            value = value[part]
        return value
    if len(parts) == 1 and hasattr(obs, parts[0]):
        return getattr(obs, parts[0])
    return None


class RuleMatcher:
    def __init__(self, store: RuleStore, fuzzy_judge=None):
        """fuzzy_judge: callable(observation, value_text) -> bool, the
        existing triage call this delegates "fuzzy" evaluation to. None
        means fuzzy predicates never match -- fails closed rather than
        guessing, consistent with every other unknown/unresolvable case
        in this matcher."""
        self.store = store
        self.fuzzy_judge = fuzzy_judge
        self._fuzzy_cache: dict[tuple[int, str], tuple[float, bool]] = {}

    def match(self, obs, observation_id: int | None = None) -> list:
        """Every active rule whose trigger.kind equals obs.kind and whose
        filters (implicit AND; empty matches everything of that kind)
        all pass."""
        candidates = self.store.list_active()
        matched = []
        for rule in candidates:
            if rule.trigger.get("kind") != obs.kind:
                continue
            filters = rule.trigger.get("filters") or []
            if self._matches_filters(obs, filters, observation_id):
                matched.append(rule)
        return matched

    def _matches_filters(self, obs, filters: list[dict], observation_id: int | None) -> bool:
        for f in filters:
            if not self._matches_one(obs, f, observation_id):
                return False
        return True

    def _matches_one(self, obs, f: dict, observation_id: int | None) -> bool:
        field = f.get("field")
        op = f.get("op")
        value = f.get("value")
        actual = _resolve_field(obs, field) if field else None

        if op == "matches":
            pattern = _compiled_regex(value) if isinstance(value, str) else None
            return bool(pattern and isinstance(actual, str) and pattern.search(actual))
        if op == "fuzzy":
            return self._fuzzy_match(obs, observation_id, value)

        handler = _COMPARISON_OPS.get(op)
        if handler is None:
            log.warning("Unknown filter op %r -- treating as no-match", op)
            return False
        try:
            return bool(handler(actual, value))
        except TypeError:
            return False

    def _fuzzy_match(self, obs, observation_id: int | None, value) -> bool:
        if observation_id is None:
            return False
        key = (observation_id, value)
        now = time.time()
        cached = self._fuzzy_cache.get(key)
        if cached is not None:
            cached_at, verdict = cached
            if now - cached_at < _FUZZY_CACHE_TTL_SECONDS:
                return verdict
            return False  # stale entry -- evaluate to False, don't refresh (Appendix A.3)
        if self.fuzzy_judge is None:
            return False
        verdict = bool(self.fuzzy_judge(obs, value))
        self._fuzzy_cache[key] = (now, verdict)
        return verdict
