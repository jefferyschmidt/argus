from unittest.mock import patch

from argus.google_calendar import NotAuthorized
from argus.spine.sensors.calendar import CalendarSensor


def test_not_configured_returns_nothing():
    with patch("argus.google_calendar.is_configured", return_value=False):
        assert CalendarSensor().poll() == []


def test_not_authorized_returns_nothing_without_raising():
    with patch("argus.google_calendar.is_configured", return_value=True), \
         patch("argus.google_calendar.list_upcoming_events", side_effect=NotAuthorized("nope")):
        assert CalendarSensor().poll() == []


def test_events_emit_calendar_event_upcoming_with_dedupe_key():
    events = [{"id": "abc123", "summary": "Standup", "start": "2026-09-02T09:00:00Z", "location": ""}]
    with patch("argus.google_calendar.is_configured", return_value=True), \
         patch("argus.google_calendar.list_upcoming_events", return_value=events):
        observations = CalendarSensor().poll()

    assert len(observations) == 1
    obs = observations[0]
    assert obs.kind == "calendar.event_upcoming"
    assert obs.subject == "Standup"
    assert obs.payload == events[0]
    assert obs.dedupe_key == "calendar:abc123:2026-09-02T09:00:00Z"


def test_rescheduled_event_gets_a_new_dedupe_key():
    original = [{"id": "abc123", "summary": "Standup", "start": "2026-09-02T09:00:00Z"}]
    rescheduled = [{"id": "abc123", "summary": "Standup", "start": "2026-09-02T10:00:00Z"}]
    with patch("argus.google_calendar.is_configured", return_value=True):
        with patch("argus.google_calendar.list_upcoming_events", return_value=original):
            first = CalendarSensor().poll()
        with patch("argus.google_calendar.list_upcoming_events", return_value=rescheduled):
            second = CalendarSensor().poll()

    assert first[0].dedupe_key != second[0].dedupe_key
