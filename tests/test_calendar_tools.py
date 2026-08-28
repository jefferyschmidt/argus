from unittest.mock import patch

from argus.tools.calendar import _create_calendar_event, _list_calendar_events


def test_list_reports_not_configured_cleanly():
    with patch("argus.google_calendar.is_configured", return_value=False):
        result = _list_calendar_events({})
    assert result.startswith("error:")
    assert "not set up" in result or "isn't set up" in result


def test_create_reports_not_configured_cleanly():
    with patch("argus.google_calendar.is_configured", return_value=False):
        result = _create_calendar_event({"summary": "s", "start": "2026-01-01T09:00:00-05:00", "end": "2026-01-01T10:00:00-05:00"})
    assert result.startswith("error:")


def test_list_formats_events():
    fake_events = [
        {"id": "1", "summary": "Dentist", "start": "2026-01-05T09:00:00-05:00", "location": ""},
        {"id": "2", "summary": "Team sync", "start": "2026-01-06T13:00:00-05:00", "location": "Zoom"},
    ]
    with patch("argus.google_calendar.is_configured", return_value=True), \
         patch("argus.google_calendar.list_upcoming_events", return_value=fake_events):
        result = _list_calendar_events({})
    assert "Dentist" in result
    assert "Team sync" in result
    assert "Zoom" in result


def test_list_with_no_events():
    with patch("argus.google_calendar.is_configured", return_value=True), \
         patch("argus.google_calendar.list_upcoming_events", return_value=[]):
        result = _list_calendar_events({})
    assert "No upcoming events" in result


def test_create_success_returns_link():
    with patch("argus.google_calendar.is_configured", return_value=True), \
         patch("argus.google_calendar.create_event", return_value={"id": "abc", "link": "https://calendar.google.com/event?abc"}):
        result = _create_calendar_event({
            "summary": "Lunch with Sam", "start": "2026-01-05T12:00:00-05:00", "end": "2026-01-05T13:00:00-05:00",
        })
    assert "Lunch with Sam" in result
    assert "https://calendar.google.com/event?abc" in result


def test_not_authorized_surfaces_cleanly_not_as_a_crash():
    from argus.google_calendar import NotAuthorized

    with patch("argus.google_calendar.is_configured", return_value=True), \
         patch("argus.google_calendar.list_upcoming_events", side_effect=NotAuthorized("run auth first")):
        result = _list_calendar_events({})
    assert result.startswith("error:")
    assert "run auth first" in result
