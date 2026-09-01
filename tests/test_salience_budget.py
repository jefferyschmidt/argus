from argus.salience.budget import InterruptionBudget


def _budget(tmp_path, cap=3) -> InterruptionBudget:
    return InterruptionBudget(tmp_path / "argus.db")


def test_available_up_to_cap(tmp_path, monkeypatch):
    monkeypatch.setattr("argus.salience.budget.settings.interruptions_per_hour", 3)
    budget = _budget(tmp_path)
    now = 1_000_000.0

    assert budget.available(now) is True
    budget.consume(now)
    assert budget.available(now) is True
    budget.consume(now)
    assert budget.available(now) is True
    budget.consume(now)
    assert budget.available(now) is False


def test_remaining_counts_down(tmp_path, monkeypatch):
    monkeypatch.setattr("argus.salience.budget.settings.interruptions_per_hour", 3)
    budget = _budget(tmp_path)
    now = 1_000_000.0

    assert budget.remaining(now) == 3
    budget.consume(now)
    assert budget.remaining(now) == 2


def test_consumption_older_than_an_hour_no_longer_counts(tmp_path, monkeypatch):
    monkeypatch.setattr("argus.salience.budget.settings.interruptions_per_hour", 1)
    budget = _budget(tmp_path)
    old = 1_000_000.0
    budget.consume(old)

    assert budget.available(old) is False
    assert budget.available(old + 3601) is True


def test_budget_does_not_refill_across_a_restart(tmp_path, monkeypatch):
    monkeypatch.setattr("argus.salience.budget.settings.interruptions_per_hour", 1)
    db_path = tmp_path / "argus.db"
    now = 1_000_000.0

    first = InterruptionBudget(db_path)
    first.consume(now)
    assert first.available(now) is False

    reopened = InterruptionBudget(db_path)  # simulates a process restart
    assert reopened.available(now) is False
