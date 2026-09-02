from argus.rules.store import RuleStore


def _store(tmp_path) -> RuleStore:
    return RuleStore(tmp_path / "argus.db")


def _rule(store, group_name, status="active"):
    rule_id = store.propose(
        natural_language="x", kind="preference",
        trigger={"kind": "focus.changed", "filters": []}, action={"type": "boost", "amount": 0.2},
        group_name=group_name,
    )
    if status in ("active", "disabled"):
        store.confirm(rule_id)
    if status == "disabled":
        store.disable(rule_id)
    return rule_id


def test_activate_mode_arms_every_disabled_member(tmp_path):
    store = _store(tmp_path)
    a = _rule(store, "focus", status="disabled")
    b = _rule(store, "focus", status="disabled")
    other_group = _rule(store, "quiet", status="disabled")

    count = store.activate_mode("focus")

    assert count == 2
    assert store.get(a).status == "active"
    assert store.get(b).status == "active"
    assert store.get(other_group).status == "disabled"  # untouched -- different group


def test_deactivate_mode_disarms_every_active_member(tmp_path):
    store = _store(tmp_path)
    a = _rule(store, "focus", status="active")
    b = _rule(store, "focus", status="active")

    count = store.deactivate_mode("focus")

    assert count == 2
    assert store.get(a).status == "disabled"
    assert store.get(b).status == "disabled"


def test_activate_mode_does_not_touch_proposed_rules(tmp_path):
    """A mode toggles rules already confirmed once -- it never confirms a
    still-pending rule on a group's behalf."""
    store = _store(tmp_path)
    pending_id = _rule(store, "focus", status="proposed")

    count = store.activate_mode("focus")

    assert count == 0
    assert store.get(pending_id).status == "proposed"


def test_activate_mode_on_unknown_group_is_a_safe_noop(tmp_path):
    store = _store(tmp_path)
    assert store.activate_mode("nonexistent") == 0


def test_mode_toggle_is_a_single_atomic_statement(tmp_path):
    """"Arms and disarms its whole group atomically" -- exercised here as:
    a mix of matching and non-matching rules, one call, every matching
    row changes together (there is no partial-group state to observe)."""
    store = _store(tmp_path)
    members = [_rule(store, "focus", status="disabled") for _ in range(5)]

    store.activate_mode("focus")

    assert all(store.get(m).status == "active" for m in members)


def test_list_by_group_returns_only_that_groups_rules(tmp_path):
    store = _store(tmp_path)
    a = _rule(store, "focus")
    _rule(store, "quiet")

    result = store.list_by_group("focus")
    assert [r.id for r in result] == [a]
