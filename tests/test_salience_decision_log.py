from argus.salience.decision_log import DecisionLog


def _log(tmp_path) -> DecisionLog:
    return DecisionLog(tmp_path / "argus.db")


def test_last_with_no_history_is_none(tmp_path):
    assert _log(tmp_path).last() is None


def test_record_and_last(tmp_path):
    log = _log(tmp_path)
    log.record(kind="mail.received", subject="a@x.com", action="hold", reason="score 0.19 below ambient threshold 0.3")

    last = log.last()
    assert last.kind == "mail.received"
    assert last.subject == "a@x.com"
    assert last.action == "hold"
    assert "0.19" in last.reason


def test_last_returns_the_most_recent_entry(tmp_path):
    log = _log(tmp_path)
    log.record(kind="mail.received", subject=None, action="hold", reason="first")
    log.record(kind="reminder.due", subject=None, action="speak", reason="second")

    assert log.last().reason == "second"


def test_recent_orders_newest_first(tmp_path):
    log = _log(tmp_path)
    log.record(kind="a", subject=None, action="hold", reason="1")
    log.record(kind="b", subject=None, action="hold", reason="2")
    log.record(kind="c", subject=None, action="hold", reason="3")

    reasons = [d.reason for d in log.recent()]
    assert reasons == ["3", "2", "1"]


def test_persists_across_restart(tmp_path):
    db_path = tmp_path / "argus.db"
    log = DecisionLog(db_path)
    log.record(kind="mail.received", subject=None, action="suppress", reason="suppressed by rule 1")

    reopened = DecisionLog(db_path)
    assert reopened.last().reason == "suppressed by rule 1"
