"""PRD.md §3.5. Constructs and starts every enabled Phase A sensor on its
own daemon thread -- same construct/start() split as ProactiveEngine
(proactive_engine.py), for the same reason: a caller can construct the
engine (and reach individual sensors) before committing to actually
running them, e.g. in tests."""

import threading

from argus.spine.sensors.base import Sensor
from argus.spine.store import SpineStore


class SpineEngine:
    def __init__(self, store: SpineStore | None = None, sensors: list[Sensor] | None = None):
        self.store = store or SpineStore()
        self._stop = threading.Event()
        # `sensors` is injectable for tests; the real default list is
        # built incrementally in _default_sensors() as each sensor from
        # PRD §3.4 is implemented.
        self.sensors: list[Sensor] = sensors if sensors is not None else self._default_sensors()

    def _default_sensors(self) -> list[Sensor]:
        from argus.spine.sensors.argus_health import ArgusHealthSensor
        from argus.spine.sensors.mail import MailSensor
        from argus.spine.sensors.window_focus import WindowFocusSensor

        return [
            WindowFocusSensor(),
            MailSensor(),
            ArgusHealthSensor(),
        ]

    def start(self) -> None:
        for sensor in self.sensors:
            threading.Thread(target=sensor.run, args=(self.store, self._stop), daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
