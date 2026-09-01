import threading
import time

from argus.spine.observation import Observation
from argus.spine.sensors.base import Sensor
from argus.spine.store import SpineStore


class _GoodSensor(Sensor):
    name = "good"
    interval_seconds = 0.01

    def __init__(self):
        self.polls = 0

    def poll(self):
        self.polls += 1
        return [Observation(source=self.name, kind="git.commit", ts=time.time(), dedupe_key=f"good-{self.polls}")]


class _RaisingSensor(Sensor):
    name = "bad"
    interval_seconds = 0.01

    def __init__(self):
        self.polls = 0

    def poll(self):
        self.polls += 1
        raise RuntimeError("sensor exploded")


def _run_briefly(sensor: Sensor, store: SpineStore) -> None:
    stop = threading.Event()
    t = threading.Thread(target=sensor.run, args=(store, stop))
    t.start()
    time.sleep(0.1)
    stop.set()
    t.join(timeout=2)


def test_poll_raising_is_logged_and_loop_continues(tmp_path):
    store = SpineStore(tmp_path / "spine.db")
    sensor = _RaisingSensor()

    _run_briefly(sensor, store)

    assert sensor.polls >= 2  # the loop kept going past the first raise
    assert store.count() == 0


def test_good_sensor_unaffected_by_a_raising_sensor(tmp_path):
    store = SpineStore(tmp_path / "spine.db")
    good = _GoodSensor()
    bad = _RaisingSensor()
    stop = threading.Event()

    threads = [
        threading.Thread(target=good.run, args=(store, stop)),
        threading.Thread(target=bad.run, args=(store, stop)),
    ]
    for t in threads:
        t.start()
    time.sleep(0.1)
    stop.set()
    for t in threads:
        t.join(timeout=2)

    assert good.polls >= 2
    assert store.count() >= 2


def test_run_stops_promptly_when_stop_event_set(tmp_path):
    store = SpineStore(tmp_path / "spine.db")
    sensor = _GoodSensor()
    sensor.interval_seconds = 5.0  # would hang the test if stop weren't respected on first wait
    stop = threading.Event()
    t = threading.Thread(target=sensor.run, args=(store, stop))
    t.start()
    time.sleep(0.05)
    stop.set()
    t.join(timeout=1)
    assert not t.is_alive()
