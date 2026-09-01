from argus.salience.held import HeldQueue


def _queue(tmp_path) -> HeldQueue:
    return HeldQueue(tmp_path / "argus.db")


def test_add_and_pending(tmp_path):
    queue = _queue(tmp_path)
    item_id = queue.add(kind="mail.received", subject="a@x.com", text="mail from a", score=0.4)

    pending = queue.pending()
    assert len(pending) == 1
    assert pending[0].id == item_id
    assert pending[0].text == "mail from a"
    assert pending[0].delivered_ts is None
    assert pending[0].dismissed_ts is None


def test_pending_orders_by_score_descending(tmp_path):
    queue = _queue(tmp_path)
    low = queue.add(kind="git.branch_stale", subject=None, text="low", score=0.1)
    high = queue.add(kind="reminder.due", subject=None, text="high", score=0.9)

    pending = queue.pending()
    assert [i.id for i in pending] == [high, low]


def test_mark_delivered_removes_from_pending(tmp_path):
    queue = _queue(tmp_path)
    item_id = queue.add(kind="mail.received", subject=None, text="x", score=0.4)

    assert queue.mark_delivered(item_id) is True
    assert queue.pending() == []


def test_dismiss_removes_from_pending(tmp_path):
    queue = _queue(tmp_path)
    item_id = queue.add(kind="mail.received", subject=None, text="x", score=0.4)

    assert queue.dismiss(item_id) is True
    assert queue.pending() == []


def test_mark_delivered_twice_returns_false_second_time(tmp_path):
    queue = _queue(tmp_path)
    item_id = queue.add(kind="mail.received", subject=None, text="x", score=0.4)
    queue.mark_delivered(item_id)
    assert queue.mark_delivered(item_id) is False


def test_dismiss_nonexistent_returns_false(tmp_path):
    assert _queue(tmp_path).dismiss(999) is False


def test_persists_across_restart(tmp_path):
    db_path = tmp_path / "argus.db"
    queue = HeldQueue(db_path)
    item_id = queue.add(kind="mail.received", subject=None, text="x", score=0.4)

    reopened = HeldQueue(db_path)
    assert [i.id for i in reopened.pending()] == [item_id]
