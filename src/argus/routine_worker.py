import logging
import time
from datetime import datetime

from argus.ui import events as ui_events

log = logging.getLogger(__name__)

_POLL_SECONDS = 60.0


class RoutineWorker:
    """Fires scheduled routines (README items 9 + 10 -- daily briefing is
    just a routine, not a separate feature) at their configured time each
    day. Unlike the other background workers (reminders, context
    awareness, email), a routine's goal runs through the FULL tool-using
    conversational pipeline (orchestrator.handle_streaming), not the
    cheap local/Groq tier -- a morning briefing needs web search, reminder
    lookups, email checks, real tool calls, not a single classification."""

    def __init__(self, orchestrator, speak_fn, interaction_lock):
        self.orchestrator = orchestrator
        self._speak_fn = speak_fn
        self._interaction_lock = interaction_lock

    def run(self) -> None:
        while True:
            time.sleep(_POLL_SECONDS)
            try:
                self.check_now()
            except Exception:
                log.exception("Routine check failed")

    def check_now(self) -> None:
        from argus.memory.routines import RoutineStore
        from argus.memory.store import get_connection

        conn = get_connection()
        try:
            due = RoutineStore(conn).list_due(datetime.now())
        finally:
            conn.close()

        for routine in due:
            if not self._interaction_lock.acquire(blocking=False):
                break  # busy with something else; this routine tries again next poll
            try:
                self._run_routine(routine)
            finally:
                self._interaction_lock.release()

    def _run_routine(self, routine) -> None:
        from argus.memory.routines import RoutineStore
        from argus.memory.store import get_connection

        conn = get_connection()
        try:
            RoutineStore(conn).mark_run(routine["id"], datetime.now())
        finally:
            conn.close()

        def on_sentence(sentence: str) -> None:
            ui_events.publish({"type": "caption", "text": sentence})
            self._speak_fn(sentence)

        try:
            self.orchestrator.handle_streaming(routine["goal"], on_sentence=on_sentence)
        except Exception:
            # Marked as run for today already (see above) -- avoids a
            # broken routine retrying every poll for the rest of the day.
            # The tradeoff is it silently skips today on failure, so at
            # least surface that rather than leaving no signal at all.
            log.exception("Routine #%s failed: %s", routine["id"], routine["goal"])
            failure_note = f"A scheduled routine failed to run: {routine['goal']}"
            ui_events.publish({"type": "caption", "text": failure_note})
            self._speak_fn(failure_note)
