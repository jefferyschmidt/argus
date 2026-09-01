"""PRD.md §3.4. Source of logic: argus/routine_worker.py -- reports the
fact that a routine is due; RoutineWorker keeps sole ownership of actually
running it and marking it run (that's judgment/action, not detection)."""

import time
from datetime import datetime

from argus.spine.observation import Observation
from argus.spine.sensors.base import Sensor


class RoutineSensor(Sensor):
    name = "routine"

    def poll(self) -> list[Observation]:
        from argus.memory.routines import RoutineStore
        from argus.memory.store import get_connection

        now = datetime.now()
        conn = get_connection()
        try:
            due = RoutineStore(conn).list_due(now)
        finally:
            conn.close()

        today = now.date().isoformat()
        return [
            Observation(
                source=self.name, kind="routine.due", ts=time.time(),
                payload={"goal": row["goal"]},
                # Date-scoped: a routine still due tomorrow (routine_worker
                # hasn't marked it run yet) should be reported again then.
                dedupe_key=f"routine:{row['id']}:{today}",
            )
            for row in due
        ]
