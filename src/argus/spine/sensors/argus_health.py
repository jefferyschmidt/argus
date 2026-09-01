"""PRD.md §3.4. New (no existing worker does this): surfaces integration
and credential failures, and spend, onto the spine. The Yahoo
AUTHENTICATIONFAILED observed 2026-08-31 (see MailSensor) is the
motivating case.

report_failure() is a small thread-safe queue other sensors can push
into -- a failure can be reported from whichever thread noticed it
without that caller needing a SpineStore reference of its own; this
sensor just drains the queue each poll."""

import json
import logging
import threading
import time

from argus.config import settings
from argus.spine.observation import Observation
from argus.spine.sensors.base import Sensor

log = logging.getLogger(__name__)

_FAILURE_KINDS = ("argus.integration_failed", "argus.credential_failed")
_lock = threading.Lock()
_queue: list[tuple[str, str | None, dict]] = []


def report_failure(kind: str, subject: str | None = None, payload: dict | None = None) -> None:
    if kind not in _FAILURE_KINDS:
        log.warning("Unknown health event kind %r ignored", kind)
        return
    with _lock:
        _queue.append((kind, subject, payload or {}))


class ArgusHealthSensor(Sensor):
    name = "argus.health"

    def __init__(self):
        self._last_spend_usd: float | None = None

    def poll(self) -> list[Observation]:
        observations = list(self._drain_queue())
        spend_obs = self._check_spend()
        if spend_obs is not None:
            observations.append(spend_obs)
        return observations

    def _drain_queue(self) -> list[Observation]:
        with _lock:
            items = list(_queue)
            _queue.clear()
        now = time.time()
        return [Observation(source=self.name, kind=kind, ts=now, subject=subject, payload=payload)
                for kind, subject, payload in items]

    def _check_spend(self) -> Observation | None:
        path = settings.data_dir / "spend.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            log.exception("Failed to read spend.json")
            return None
        spend = data.get("spend_usd")
        if not isinstance(spend, (int, float)):
            return None
        if self._last_spend_usd is not None and spend <= self._last_spend_usd:
            return None
        self._last_spend_usd = spend
        return Observation(source=self.name, kind="argus.spend_recorded", ts=time.time(), payload={"usd": spend})
