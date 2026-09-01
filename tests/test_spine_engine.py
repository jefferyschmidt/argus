import time

from argus.spine.engine import SpineEngine
from argus.spine.observation import Observation
from argus.spine.sensors.base import Sensor
from argus.spine.store import SpineStore


class _CountingSensor(Sensor):
    name = "counting"
    interval_seconds = 0.01

    def __init__(self):
        self.polls = 0

    def poll(self):
        self.polls += 1
        return [Observation(source=self.name, kind="git.commit", ts=time.time(), dedupe_key=f"c-{self.polls}")]


def test_default_sensors_is_empty_until_units_4_and_5_populate_it(tmp_path):
    engine = SpineEngine(store=SpineStore(tmp_path / "spine.db"))
    assert engine.sensors == []


def test_start_runs_every_injected_sensor_on_its_own_thread(tmp_path):
    store = SpineStore(tmp_path / "spine.db")
    sensors = [_CountingSensor(), _CountingSensor()]
    engine = SpineEngine(store=store, sensors=sensors)

    engine.start()
    time.sleep(0.1)
    engine.stop()

    assert all(s.polls >= 1 for s in sensors)
    assert store.count() >= 2
