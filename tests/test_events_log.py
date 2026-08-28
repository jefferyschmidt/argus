import json
from unittest.mock import patch

from argus.ui import events as ui_events


def test_publish_appends_event_to_the_on_disk_log(tmp_path):
    """Confirmed directly requested: a way to review sessions for rough
    spots without adding new instrumentation at every call site -- every
    meaningful thing already flows through publish(), so logging here
    gives full session-review capability for free."""
    log_dir = tmp_path / "events"
    with patch("argus.ui.events._event_log_path", return_value=log_dir / "events-test.jsonl"):
        log_dir.mkdir(parents=True, exist_ok=True)
        ui_events.publish({"type": "transcript", "role": "you", "text": "hello"})

    lines = (log_dir / "events-test.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["type"] == "transcript"
    assert record["role"] == "you"
    assert record["text"] == "hello"
    assert "ts" in record


def test_publish_still_notifies_subscribers_alongside_logging(tmp_path):
    log_dir = tmp_path / "events"
    with patch("argus.ui.events._event_log_path", return_value=log_dir / "events-test.jsonl"):
        log_dir.mkdir(parents=True, exist_ok=True)
        q = ui_events.subscribe()
        try:
            ui_events.publish({"type": "state", "value": "idle"})
            assert q.get_nowait() == {"type": "state", "value": "idle"}
        finally:
            ui_events.unsubscribe(q)


def test_publish_never_raises_if_the_log_write_fails():
    with patch("argus.ui.events._event_log_path", side_effect=OSError("disk full")):
        ui_events.publish({"type": "state", "value": "idle"})  # must not raise
