"""PRD.md §3.4 + §19 unit 38. Source of logic: argus/memory/reminders.py.
Uses the existing shared argus.db (memory.store.get_connection) rather
than the spine's own db -- that's where reminders already live; only the
spine db itself needs the separate-connection treatment (P1).

Observe only, never decide or mutate (§0/§3.3): poll() used to call
mark_notified() itself, right here, regardless of whether anything ever
actually turned the resulting reminder.due observation into speech --
Argus's whole delivery decision was made by a sensor that has no way to
know whether delivery happened. A held/undelivered reminder was marked
handled anyway and silently lost. mark_notified() now belongs solely to
ProactiveEngine's reminder tick step (unit 37), called only after
Decision.delivered confirms the reminder was actually spoken. This
sensor's own observation is deduped (dedupe_key) so it still reports
each reminder exactly once for the timeline, independent of delivery."""

import time
from datetime import datetime, timezone

from argus.spine.observation import Observation
from argus.spine.sensors.base import Sensor


class ReminderSensor(Sensor):
    name = "reminder"

    def poll(self) -> list[Observation]:
        from argus.memory.reminders import ReminderStore
        from argus.memory.store import get_connection

        conn = get_connection()
        try:
            store = ReminderStore(conn)
            now_iso = datetime.now(timezone.utc).isoformat()
            due = store.list_due(now_iso)
            return [
                Observation(
                    source=self.name, kind="reminder.due", ts=time.time(),
                    payload={"text": row["text"], "due_at": row["due_at"]},
                    dedupe_key=f"reminder:{row['id']}",
                )
                for row in due
            ]
        finally:
            conn.close()
