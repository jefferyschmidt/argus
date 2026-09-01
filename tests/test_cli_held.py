from argus.cli import held
from argus.salience.held import HeldQueue


def _queue(tmp_path, monkeypatch) -> HeldQueue:
    queue = HeldQueue(tmp_path / "argus.db")
    # held() imports HeldQueue locally (`from argus.salience.held import
    # HeldQueue`), so the patch target is the source module's attribute.
    monkeypatch.setattr("argus.salience.held.HeldQueue", lambda: queue)
    return queue


def test_held_list_with_nothing_pending(tmp_path, monkeypatch, capsys):
    _queue(tmp_path, monkeypatch)

    held("list", None)

    assert "Nothing held." in capsys.readouterr().out


def test_held_list_shows_pending_items(tmp_path, monkeypatch, capsys):
    queue = _queue(tmp_path, monkeypatch)
    queue.add(kind="mail.received", subject="a@x.com", text="you've got mail", score=0.4)

    held("list", None)

    out = capsys.readouterr().out
    assert "you've got mail" in out
    assert "a@x.com" in out


def test_held_dismiss_removes_the_item(tmp_path, monkeypatch, capsys):
    queue = _queue(tmp_path, monkeypatch)
    item_id = queue.add(kind="mail.received", subject=None, text="x", score=0.4)

    held("dismiss", item_id)

    assert f"Dismissed held item #{item_id}" in capsys.readouterr().out
    assert queue.pending() == []


def test_held_dismiss_missing_id_prints_usage(tmp_path, monkeypatch, capsys):
    _queue(tmp_path, monkeypatch)

    held("dismiss", None)

    assert "Usage:" in capsys.readouterr().out


def test_held_dismiss_unknown_id_reports_error(tmp_path, monkeypatch, capsys):
    _queue(tmp_path, monkeypatch)

    held("dismiss", 999)

    assert "No pending held item" in capsys.readouterr().out
