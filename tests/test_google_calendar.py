from unittest.mock import MagicMock, patch

from argus.google_calendar import list_upcoming_events


def test_list_upcoming_events_includes_end_time():
    fake_service = MagicMock()
    fake_service.events.return_value.list.return_value.execute.return_value = {
        "items": [{
            "id": "1", "summary": "Standup",
            "start": {"dateTime": "2026-09-02T09:00:00-04:00"},
            "end": {"dateTime": "2026-09-02T09:30:00-04:00"},
            "location": "Zoom",
        }]
    }
    with patch("argus.google_calendar._service", return_value=fake_service):
        events = list_upcoming_events()

    assert events == [{
        "id": "1", "summary": "Standup",
        "start": "2026-09-02T09:00:00-04:00", "end": "2026-09-02T09:30:00-04:00",
        "location": "Zoom",
    }]


def test_list_upcoming_events_all_day_uses_date_not_datetime():
    fake_service = MagicMock()
    fake_service.events.return_value.list.return_value.execute.return_value = {
        "items": [{"id": "1", "summary": "Holiday", "start": {"date": "2026-09-02"}, "end": {"date": "2026-09-03"}}]
    }
    with patch("argus.google_calendar._service", return_value=fake_service):
        events = list_upcoming_events()

    assert events[0]["start"] == "2026-09-02"
    assert events[0]["end"] == "2026-09-03"
