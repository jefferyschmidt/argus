"""PRD.md §3.4. Thin extraction of context_awareness._active_window_title()'s
detection half -- ContextAwarenessWorker keeps its own judgment (whether to
say something about a window) and announcement code exactly where it is;
this sensor only records the bare fact of what's focused.

Idle detection: pygetwindow.getActiveWindow() returning None (a locked
screen, a moment between windows) is the only idle-adjacent signal this
codebase already surfaces, so that's what idle_started/idle_ended are
keyed on here."""

import time

from argus.config import settings
from argus.spine.observation import Observation
from argus.spine.sensors.base import Sensor


class WindowFocusSensor(Sensor):
    name = "window_focus"

    def __init__(self):
        # Matches ContextAwarenessWorker's own scan cadence -- there's no
        # reason to poll the active window at a different rate than the
        # worker that already does so for judgment purposes.
        self.interval_seconds = settings.proactive_context_scan_seconds
        self._current_title: str | None = None
        self._idle = False

    def poll(self) -> list[Observation]:
        from argus.context_awareness import _active_window_title

        title = _active_window_title()
        now = time.time()
        observations: list[Observation] = []

        if title is None:
            if not self._idle:
                self._idle = True
                observations.append(Observation(source=self.name, kind="focus.idle_started", ts=now))
            return observations

        if self._idle:
            self._idle = False
            observations.append(Observation(source=self.name, kind="focus.idle_ended", ts=now))

        if title != self._current_title:
            self._current_title = title
            observations.append(Observation(source=self.name, kind="focus.changed", ts=now, subject=title))

        return observations
