"""PRD.md §3.4. Source of logic: argus/memory/reminders.py. Uses the
existing shared argus.db (memory.store.get_connection) rather than the
spine's own db -- that's where reminders already live; only the spine
db itself needs the separate-connection treatment (P1)."""

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
            observations = []
            for row in due:
                observations.append(Observation(
                    source=self.name, kind="reminder.due", ts=time.time(),
                    payload={"text": row["text"], "due_at": row["due_at"]},
                    dedupe_key=f"reminder:{row['id']}",
                ))
                # ReminderStore's own notified flag is what actually keeps
                # this from re-firing every poll -- dedupe_key is a second,
                # belt-and-braces guard, not the primary mechanism here.
                store.mark_notified(row["id"])
            return observations
        finally:
            conn.close()
