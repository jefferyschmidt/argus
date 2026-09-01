"""PRD.md §3.3. A Sensor only detects and reports facts -- see §3.4's
"leave the worker's judgment and announcement code exactly where it is."
No sensor may call an LLM (PRD §0 non-negotiable rules)."""

import logging
import threading
from abc import ABC, abstractmethod
from collections.abc import Iterable

from argus.spine.observation import Observation
from argus.spine.store import SpineStore

log = logging.getLogger(__name__)


class Sensor(ABC):
    name: str
    interval_seconds: float = 60.0

    @abstractmethod
    def poll(self) -> Iterable[Observation]:
        """Return the Observations detected since the last poll. May
        raise -- run() below is what's responsible for never letting that
        escape the loop."""

    def run(self, store: SpineStore, stop: threading.Event) -> None:
        """Poll loop. MUST NOT raise -- catches everything from poll(),
        logs it, and continues to the next interval (PRD §0: "every
        background thread catches and logs everything. No exception may
        take down the others.")."""
        while not stop.is_set():
            try:
                for obs in self.poll():
                    store.record(obs)
            except Exception:
                log.exception("Sensor %s poll failed", getattr(self, "name", type(self).__name__))
            stop.wait(self.interval_seconds)
