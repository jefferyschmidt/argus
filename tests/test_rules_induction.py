import time

from argus.rules.induction import InductionEngine
from argus.rules.store import RuleStore
from argus.salience.held import HeldQueue


def _engine(tmp_path) -> InductionEngine:
    return InductionEngine(RuleStore(tmp_path / "argus.db"), HeldQueue(tmp_path / "argus.db"))


def _dismiss(held: HeldQueue, kind: str, n: int, score: float = 0.4) -> None:
    for _ in range(n):
        item_id = held.add(kind=kind, subject=None, text="x", score=score)
        held.dismiss(item_id)


def test_no_dismissals_proposes_nothing(tmp_path):
    engine = _engine(tmp_path)
    assert engine.run_once() == []


def test_below_threshold_proposes_nothing(tmp_path):
    engine = _engine(tmp_path)
    _dismiss(engine.held, "git.branch_stale", 2)  # below _DISMISSAL_THRESHOLD (3)

    assert engine.run_once() == []


def test_repeated_dismissals_propose_a_suppression_rule(tmp_path):
    engine = _engine(tmp_path)
    _dismiss(engine.held, "git.branch_stale", 3)

    proposed = engine.run_once()

    assert len(proposed) == 1
    rule = engine.rule_store.get(proposed[0])
    assert rule.status == "proposed"
    assert rule.origin == "induced"
    assert rule.kind == "suppression"
    assert rule.trigger == {"kind": "git.branch_stale", "filters": []}
    assert rule.action == {"type": "suppress"}


def test_pending_not_dismissed_items_do_not_count(tmp_path):
    engine = _engine(tmp_path)
    for _ in range(5):
        engine.held.add(kind="git.branch_stale", subject=None, text="x", score=0.4)  # never dismissed

    assert engine.run_once() == []


def test_delivered_items_do_not_count_as_dismissals(tmp_path):
    engine = _engine(tmp_path)
    for _ in range(5):
        item_id = engine.held.add(kind="git.branch_stale", subject=None, text="x", score=0.4)
        engine.held.mark_delivered(item_id)

    assert engine.run_once() == []


# -- the critical guarantee ---------------------------------------------

def test_induced_rule_cannot_reach_active_without_explicit_confirmation(tmp_path):
    """G4 (PRD §7.5): proposes only, never activates. An induced rule
    must go through the identical confirmation path as a user-authored
    one -- nothing in InductionEngine may call confirm() itself."""
    engine = _engine(tmp_path)
    _dismiss(engine.held, "git.branch_stale", 3)

    (rule_id,) = engine.run_once()

    # Immediately after induction: still proposed, not active.
    assert engine.rule_store.get(rule_id).status == "proposed"

    # Running induction again (e.g. the next scheduled daily pass) must
    # not somehow flip it active either.
    engine.run_once()
    assert engine.rule_store.get(rule_id).status == "proposed"

    # It only ever becomes active via an explicit, separate confirm() call.
    assert engine.rule_store.confirm(rule_id) is True
    assert engine.rule_store.get(rule_id).status == "active"


# -- weekly cap -----------------------------------------------------------

def test_weekly_cap_limits_proposals_in_a_single_run(tmp_path, monkeypatch):
    monkeypatch.setattr("argus.rules.induction.settings.induced_rule_proposals_per_week", 2)
    engine = _engine(tmp_path)
    for kind in ("git.branch_stale", "routine.due", "focus.changed"):
        _dismiss(engine.held, kind, 3)

    proposed = engine.run_once()

    assert len(proposed) == 2  # the third qualifying kind is not proposed this run


def test_weekly_cap_counts_confirmed_induced_rules_too(tmp_path, monkeypatch):
    """The cap limits total nagging, not just still-pending proposals --
    an already-confirmed induced rule still counts against it."""
    monkeypatch.setattr("argus.rules.induction.settings.induced_rule_proposals_per_week", 1)
    engine = _engine(tmp_path)
    _dismiss(engine.held, "git.branch_stale", 3)
    (first_id,) = engine.run_once()
    engine.rule_store.confirm(first_id)

    _dismiss(engine.held, "routine.due", 3)
    proposed = engine.run_once()

    assert proposed == []


def test_weekly_cap_resets_outside_the_window(tmp_path, monkeypatch):
    """held_items.dismissed_ts and rules.created_ts are always stamped
    with real time.time(), so this ages the first proposal's created_ts
    directly (simulating "that was over a week ago") rather than faking
    `now` into the future -- which would also push the *window*
    forward and filter out the second batch's very-real, very-current
    dismissals right along with it."""
    monkeypatch.setattr("argus.rules.induction.settings.induced_rule_proposals_per_week", 1)
    engine = _engine(tmp_path)
    _dismiss(engine.held, "git.branch_stale", 3)
    proposed_first = engine.run_once()
    assert len(proposed_first) == 1

    eight_days_ago = time.time() - 8 * 86400
    engine.rule_store._conn.execute(
        "UPDATE rules SET created_ts = ? WHERE id = ?", (eight_days_ago, proposed_first[0])
    )
    engine.rule_store._conn.commit()

    _dismiss(engine.held, "routine.due", 3)
    proposed_second = engine.run_once()

    assert len(proposed_second) == 1


# -- never re-propose a pattern already suggested ----------------------

def test_does_not_re_propose_a_pending_induced_rule_for_the_same_kind(tmp_path):
    engine = _engine(tmp_path)
    _dismiss(engine.held, "git.branch_stale", 3)
    first = engine.run_once()
    assert len(first) == 1

    _dismiss(engine.held, "git.branch_stale", 3)  # more dismissals of the same kind
    second = engine.run_once()

    assert second == []
    assert len(engine.rule_store.list_by_origin("induced")) == 1


def test_does_not_re_propose_a_rejected_induced_rule(tmp_path):
    """"A proposal the user rejects is recorded so the same pattern is
    not re-proposed" (§7.5)."""
    engine = _engine(tmp_path)
    _dismiss(engine.held, "git.branch_stale", 3)
    (rule_id,) = engine.run_once()
    engine.rule_store.revoke(rule_id)

    _dismiss(engine.held, "git.branch_stale", 3)
    second = engine.run_once()

    assert second == []


# -- persistence / general shape -------------------------------------------

def test_multiple_qualifying_kinds_each_get_proposed(tmp_path):
    engine = _engine(tmp_path)
    _dismiss(engine.held, "git.branch_stale", 3)
    _dismiss(engine.held, "routine.due", 4)

    proposed = engine.run_once()

    assert len(proposed) == 2
    kinds = {engine.rule_store.get(rid).trigger["kind"] for rid in proposed}
    assert kinds == {"git.branch_stale", "routine.due"}
