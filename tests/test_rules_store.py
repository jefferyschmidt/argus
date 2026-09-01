import threading

from argus.rules.store import RuleStore


def _store(tmp_path) -> RuleStore:
    return RuleStore(tmp_path / "argus.db")


def _propose(store, **overrides):
    defaults = dict(
        natural_language="Don't tell me about newsletters",
        kind="suppression",
        trigger={"kind": "mail.received", "filters": []},
        action={"type": "suppress"},
    )
    defaults.update(overrides)
    return store.propose(**defaults)


def test_propose_creates_a_proposed_rule(tmp_path):
    store = _store(tmp_path)
    rule_id = _propose(store)

    rule = store.get(rule_id)
    assert rule.status == "proposed"
    assert rule.confirmed_ts is None
    assert rule.natural_language == "Don't tell me about newsletters"
    assert rule.trigger == {"kind": "mail.received", "filters": []}
    assert rule.hit_count == 0
    assert rule.origin == "user"


def test_confirm_activates_a_proposed_rule(tmp_path):
    store = _store(tmp_path)
    rule_id = _propose(store)

    assert store.confirm(rule_id) is True
    rule = store.get(rule_id)
    assert rule.status == "active"
    assert rule.confirmed_ts is not None


def test_confirm_on_already_active_rule_returns_false(tmp_path):
    store = _store(tmp_path)
    rule_id = _propose(store)
    store.confirm(rule_id)
    assert store.confirm(rule_id) is False


def test_disable_only_affects_active_rules(tmp_path):
    store = _store(tmp_path)
    rule_id = _propose(store)
    assert store.disable(rule_id) is False  # still proposed, not active

    store.confirm(rule_id)
    assert store.disable(rule_id) is True
    assert store.get(rule_id).status == "disabled"


def test_revoke_works_from_active_or_proposed(tmp_path):
    store = _store(tmp_path)
    proposed_id = _propose(store)
    active_id = _propose(store)
    store.confirm(active_id)

    assert store.revoke(proposed_id) is True
    assert store.revoke(active_id) is True
    assert store.get(proposed_id).status == "revoked"
    assert store.get(active_id).status == "revoked"
    assert store.get(active_id).revoked_ts is not None


def test_revoke_twice_returns_false_second_time(tmp_path):
    store = _store(tmp_path)
    rule_id = _propose(store)
    store.revoke(rule_id)
    assert store.revoke(rule_id) is False


def test_get_missing_rule_returns_none(tmp_path):
    assert _store(tmp_path).get(999) is None


def test_list_active_excludes_proposed_and_filters_by_kind(tmp_path):
    store = _store(tmp_path)
    suppression_id = _propose(store, kind="suppression")
    preference_id = _propose(store, kind="preference")
    store.confirm(suppression_id)
    store.confirm(preference_id)
    _propose(store, kind="suppression")  # left proposed

    all_active = store.list_active()
    assert {r.id for r in all_active} == {suppression_id, preference_id}

    suppressions = store.list_active(kind="suppression")
    assert [r.id for r in suppressions] == [suppression_id]


def test_list_pending_returns_only_proposed(tmp_path):
    store = _store(tmp_path)
    pending_id = _propose(store)
    active_id = _propose(store)
    store.confirm(active_id)

    pending = store.list_pending()
    assert [r.id for r in pending] == [pending_id]


def test_record_hit_increments_count_and_sets_last_fired(tmp_path):
    store = _store(tmp_path)
    rule_id = _propose(store)
    store.confirm(rule_id)

    store.record_hit(rule_id)
    store.record_hit(rule_id)

    rule = store.get(rule_id)
    assert rule.hit_count == 2
    assert rule.last_fired_ts is not None


def test_rule_round_trips_every_field_through_propose_and_confirm(tmp_path):
    store = _store(tmp_path)
    rule_id = store.propose(
        natural_language="Boost anything from Julia", source_utterance="boost julia's emails",
        kind="preference", trigger={"kind": "mail.received", "filters": [{"field": "subject", "op": "contains", "value": "julia"}]},
        action={"type": "boost", "amount": 0.3}, conditions=[{"type": "timeout", "seconds": 60}],
        until_condition={"type": "manual_only"}, group_name="focus", authorization={"scope": "email"},
        origin="induced",
    )

    rule = store.get(rule_id)
    assert rule.source_utterance == "boost julia's emails"
    assert rule.action == {"type": "boost", "amount": 0.3}
    assert rule.conditions == [{"type": "timeout", "seconds": 60}]
    assert rule.until_condition == {"type": "manual_only"}
    assert rule.group_name == "focus"
    assert rule.authorization == {"scope": "email"}
    assert rule.origin == "induced"


def test_persists_across_restart(tmp_path):
    db_path = tmp_path / "argus.db"
    store = RuleStore(db_path)
    rule_id = _propose(store)
    store.confirm(rule_id)

    reopened = RuleStore(db_path)
    rule = reopened.get(rule_id)
    assert rule.status == "active"


def test_concurrent_propose_does_not_raise_database_is_locked(tmp_path):
    store = _store(tmp_path)
    errors = []

    def _work(n: int) -> None:
        try:
            for i in range(25):
                _propose(store, natural_language=f"rule {n}-{i}")
        except Exception as e:  # pragma: no cover - failure path
            errors.append(e)

    threads = [threading.Thread(target=_work, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert len(store.list_pending()) == 6 * 25
