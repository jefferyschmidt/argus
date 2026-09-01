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


def test_rule_with_nonempty_filters_does_not_match_yet(tmp_path):
    """Base matcher (unit 15) only handles the empty-filters case; the
    full filter-op vocabulary lands in unit 15a (Appendix A.3), which
    replaces this test's expectation."""
    store = _store(tmp_path)
    rule_id = store.propose(
        natural_language="x", kind="suppression",
        trigger={"kind": "mail.received", "filters": [{"field": "subject", "op": "eq", "value": "a@x.com"}]},
        action={"type": "suppress"},
    )
    store.confirm(rule_id)

    matched = RuleMatcher(store).match(_FakeObs(kind="mail.received", subject="a@x.com"))
    assert matched == []
