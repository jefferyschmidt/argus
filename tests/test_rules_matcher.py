from dataclasses import dataclass

from argus.rules.matcher import RuleMatcher
from argus.rules.store import RuleStore


@dataclass
class _FakeObs:
    kind: str
    subject: str | None = None
    source: str = "test"
    confidence: float = 1.0
    payload: dict = None

    def __post_init__(self):
        if self.payload is None:
            self.payload = {}


def _store(tmp_path) -> RuleStore:
    return RuleStore(tmp_path / "argus.db")


def test_matches_active_rule_with_empty_filters(tmp_path):
    store = _store(tmp_path)
    rule_id = store.propose(
        natural_language="x", kind="suppression",
        trigger={"kind": "mail.received", "filters": []}, action={"type": "suppress"},
    )
    store.confirm(rule_id)

    matched = RuleMatcher(store).match(_FakeObs(kind="mail.received"))
    assert [r.id for r in matched] == [rule_id]


def test_does_not_match_a_different_kind(tmp_path):
    store = _store(tmp_path)
    rule_id = store.propose(
        natural_language="x", kind="suppression",
        trigger={"kind": "mail.received", "filters": []}, action={"type": "suppress"},
    )
    store.confirm(rule_id)

    matched = RuleMatcher(store).match(_FakeObs(kind="git.commit"))
    assert matched == []


def test_proposed_rule_is_not_matched(tmp_path):
    store = _store(tmp_path)
    store.propose(
        natural_language="x", kind="suppression",
        trigger={"kind": "mail.received", "filters": []}, action={"type": "suppress"},
    )  # never confirmed

    matched = RuleMatcher(store).match(_FakeObs(kind="mail.received"))
    assert matched == []


def test_revoked_rule_is_not_matched(tmp_path):
    store = _store(tmp_path)
    rule_id = store.propose(
        natural_language="x", kind="suppression",
        trigger={"kind": "mail.received", "filters": []}, action={"type": "suppress"},
    )
    store.confirm(rule_id)
    store.revoke(rule_id)

    matched = RuleMatcher(store).match(_FakeObs(kind="mail.received"))
    assert matched == []


def _rule(store, field, op, value, kind="mail.received"):
    rule_id = store.propose(
        natural_language="x", kind="suppression",
        trigger={"kind": kind, "filters": [{"field": field, "op": op, "value": value}]},
        action={"type": "suppress"},
    )
    store.confirm(rule_id)
    return rule_id


# -- ops: eq / neq -----------------------------------------------------

def test_op_eq_passes_and_fails(tmp_path):
    store = _store(tmp_path)
    rule_id = _rule(store, "subject", "eq", "a@x.com")
    matcher = RuleMatcher(store)

    assert [r.id for r in matcher.match(_FakeObs(kind="mail.received", subject="a@x.com"))] == [rule_id]
    assert matcher.match(_FakeObs(kind="mail.received", subject="b@x.com")) == []


def test_op_neq_passes_and_fails(tmp_path):
    store = _store(tmp_path)
    rule_id = _rule(store, "subject", "neq", "a@x.com")
    matcher = RuleMatcher(store)

    assert [r.id for r in matcher.match(_FakeObs(kind="mail.received", subject="b@x.com"))] == [rule_id]
    assert matcher.match(_FakeObs(kind="mail.received", subject="a@x.com")) == []


# -- ops: contains / not_contains ---------------------------------------

def test_op_contains_passes_and_fails(tmp_path):
    store = _store(tmp_path)
    rule_id = _rule(store, "subject", "contains", "@gov")
    matcher = RuleMatcher(store)

    assert [r.id for r in matcher.match(_FakeObs(kind="mail.received", subject="clerk@gov.example"))] == [rule_id]
    assert matcher.match(_FakeObs(kind="mail.received", subject="a@x.com")) == []


def test_op_not_contains_passes_and_fails(tmp_path):
    store = _store(tmp_path)
    rule_id = _rule(store, "subject", "not_contains", "@gov")
    matcher = RuleMatcher(store)

    assert [r.id for r in matcher.match(_FakeObs(kind="mail.received", subject="a@x.com"))] == [rule_id]
    assert matcher.match(_FakeObs(kind="mail.received", subject="clerk@gov.example")) == []


# -- op: matches (regex) -------------------------------------------------

def test_op_matches_passes_and_fails(tmp_path):
    store = _store(tmp_path)
    rule_id = _rule(store, "subject", "matches", r"^claude.*build$")
    matcher = RuleMatcher(store)

    assert [r.id for r in matcher.match(_FakeObs(kind="mail.received", subject="claude nightly build"))] == [rule_id]
    assert matcher.match(_FakeObs(kind="mail.received", subject="something else")) == []


def test_op_matches_invalid_regex_is_no_match_and_logs(tmp_path, caplog):
    import logging
    store = _store(tmp_path)
    _rule(store, "subject", "matches", "[unclosed")
    matcher = RuleMatcher(store)

    with caplog.at_level(logging.WARNING):
        matched = matcher.match(_FakeObs(kind="mail.received", subject="anything"))
    assert matched == []
    assert "Invalid regex" in caplog.text


# -- ops: gt / gte / lt / lte -------------------------------------------

def test_op_gt_gte_lt_lte(tmp_path):
    store = _store(tmp_path)
    gt_id = _rule(store, "confidence", "gt", 0.5)
    matcher = RuleMatcher(store)
    assert [r.id for r in matcher.match(_FakeObs(kind="mail.received", confidence=0.9))] == [gt_id]
    assert matcher.match(_FakeObs(kind="mail.received", confidence=0.5)) == []


def test_op_lte_passes_and_fails(tmp_path):
    store = _store(tmp_path)
    rule_id = _rule(store, "confidence", "lte", 0.5)
    matcher = RuleMatcher(store)
    assert [r.id for r in matcher.match(_FakeObs(kind="mail.received", confidence=0.5))] == [rule_id]
    assert matcher.match(_FakeObs(kind="mail.received", confidence=0.51)) == []


# -- op: in ---------------------------------------------------------------

def test_op_in_passes_and_fails(tmp_path):
    store = _store(tmp_path)
    rule_id = _rule(store, "subject", "in", ["a@x.com", "b@x.com"])
    matcher = RuleMatcher(store)
    assert [r.id for r in matcher.match(_FakeObs(kind="mail.received", subject="a@x.com"))] == [rule_id]
    assert matcher.match(_FakeObs(kind="mail.received", subject="c@x.com")) == []


# -- payload dotted path --------------------------------------------------

def test_payload_dotted_field_resolves_nested_values(tmp_path):
    store = _store(tmp_path)
    rule_id = _rule(store, "payload.usd", "gt", 50.0, kind="argus.spend_recorded")
    matcher = RuleMatcher(store)
    assert [r.id for r in matcher.match(_FakeObs(kind="argus.spend_recorded", payload={"usd": 75.0}))] == [rule_id]
    assert matcher.match(_FakeObs(kind="argus.spend_recorded", payload={"usd": 10.0})) == []


def test_unresolvable_field_is_no_match(tmp_path):
    store = _store(tmp_path)
    _rule(store, "payload.nope", "eq", "x")
    matcher = RuleMatcher(store)
    assert matcher.match(_FakeObs(kind="mail.received", payload={})) == []


# -- unknown op -------------------------------------------------------------

def test_unknown_op_is_no_match_and_logs(tmp_path, caplog):
    import logging
    store = _store(tmp_path)
    _rule(store, "subject", "between", "x")
    matcher = RuleMatcher(store)

    with caplog.at_level(logging.WARNING):
        matched = matcher.match(_FakeObs(kind="mail.received", subject="x"))
    assert matched == []
    assert "Unknown filter op" in caplog.text


# -- op: fuzzy + verdict cache (Appendix A.3) --------------------------------

def test_fuzzy_delegates_to_judge_and_caches_per_observation(tmp_path):
    store = _store(tmp_path)
    _rule(store, "self", "fuzzy", "an important email")
    _rule(store, "self", "fuzzy", "an important email")  # second rule, same predicate
    calls = []

    def judge(obs, value):
        calls.append((obs, value))
        return True

    matcher = RuleMatcher(store, fuzzy_judge=judge)
    matched = matcher.match(_FakeObs(kind="mail.received", subject="x"), observation_id=42)

    assert len(matched) == 2  # both rules matched
    assert len(calls) == 1  # but only one judge call for the shared (observation, value) key


def test_fuzzy_without_observation_id_never_matches(tmp_path):
    store = _store(tmp_path)
    _rule(store, "self", "fuzzy", "an important email")
    matcher = RuleMatcher(store, fuzzy_judge=lambda obs, value: True)

    matched = matcher.match(_FakeObs(kind="mail.received"), observation_id=None)
    assert matched == []


def test_fuzzy_without_a_judge_never_matches(tmp_path):
    store = _store(tmp_path)
    _rule(store, "self", "fuzzy", "an important email")
    matcher = RuleMatcher(store)  # no fuzzy_judge

    matched = matcher.match(_FakeObs(kind="mail.received"), observation_id=1)
    assert matched == []


def test_fuzzy_cache_expired_returns_false_without_a_fresh_call(tmp_path, monkeypatch):
    store = _store(tmp_path)
    _rule(store, "self", "fuzzy", "an important email")
    calls = []
    matcher = RuleMatcher(store, fuzzy_judge=lambda obs, value: calls.append(1) or True)

    matched_first = matcher.match(_FakeObs(kind="mail.received"), observation_id=7)
    assert len(matched_first) == 1
    assert len(calls) == 1

    # Simulate the cached verdict having aged past the TTL.
    key = (7, "an important email")
    cached_at, verdict = matcher._fuzzy_cache[key]
    matcher._fuzzy_cache[key] = (cached_at - 7200, verdict)

    matched_second = matcher.match(_FakeObs(kind="mail.received"), observation_id=7)
    assert matched_second == []  # stale entry evaluates to False
    assert len(calls) == 1  # no fresh call was made
