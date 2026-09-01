"""PRD.md §3.4. Source of logic: argus/google_calendar.py."""

import time

from argus.spine.observation import Observation
from argus.spine.sensors.base import Sensor


class CalendarSensor(Sensor):
    name = "calendar"
    interval_seconds = 300.0  # calendars change slowly; no need for mail-cadence polling

    def poll(self) -> list[Observation]:
        from argus import google_calendar

        if not google_calendar.is_configured():
            return []
        try:
            events = google_calendar.list_upcoming_events()
        except google_calendar.NotAuthorized:
            return []

        now = time.time()
        observations = []
        for event in events:
            observations.append(Observation(
                source=self.name, kind="calendar.event_upcoming", ts=now,
                subject=event.get("summary"),
                payload=event,
                # Keyed on (id, start) rather than just id -- a rescheduled
                # event should be reported again, not silently deduped
                # away by its unchanged id.
                dedupe_key=f"calendar:{event.get('id')}:{event.get('start')}",
            ))
        return observations
