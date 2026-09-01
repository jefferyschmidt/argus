import json
import logging

import pytest

from argus.spine.sensors import argus_health
from argus.spine.sensors.argus_health import ArgusHealthSensor, report_failure


@pytest.fixture(autouse=True)
def _clear_health_queue():
    argus_health._queue.clear()
    yield
    argus_health._queue.clear()


def test_report_failure_is_drained_on_next_poll(tmp_path, monkeypatch):
    monkeypatch.setattr("argus.spine.sensors.argus_health.settings.argus_data_dir", str(tmp_path))
    sensor = ArgusHealthSensor()
    report_failure("argus.credential_failed", "Yahoo", {"error": "AUTHENTICATIONFAILED"})

    observations = sensor.poll()

    assert len(observations) == 1
    assert observations[0].kind == "argus.credential_failed"
    assert observations[0].subject == "Yahoo"
    assert observations[0].payload == {"error": "AUTHENTICATIONFAILED"}


def test_report_failure_unknown_kind_is_ignored(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr("argus.spine.sensors.argus_health.settings.argus_data_dir", str(tmp_path))
    sensor = ArgusHealthSensor()
    with caplog.at_level(logging.WARNING):
        report_failure("not.a.real.kind", "x")

    assert sensor.poll() == []
    assert "Unknown health event kind" in caplog.text


def test_queue_is_drained_not_repeated(tmp_path, monkeypatch):
    monkeypatch.setattr("argus.spine.sensors.argus_health.settings.argus_data_dir", str(tmp_path))
    sensor = ArgusHealthSensor()
    report_failure("argus.integration_failed", "Spotify")

    first = sensor.poll()
    second = sensor.poll()

    assert len(first) == 1
    assert second == []


def test_spend_recorded_when_spend_json_increases(tmp_path, monkeypatch):
    monkeypatch.setattr("argus.spine.sensors.argus_health.settings.argus_data_dir", str(tmp_path))
    spend_path = tmp_path / "spend.json"
    spend_path.write_text(json.dumps({"date": "2026-09-01", "spend_usd": 1.5}))

    sensor = ArgusHealthSensor()
    first = sensor.poll()
    assert len(first) == 1
    assert first[0].kind == "argus.spend_recorded"
    assert first[0].payload == {"usd": 1.5}

    # unchanged spend -- no repeat
    assert sensor.poll() == []

    spend_path.write_text(json.dumps({"date": "2026-09-01", "spend_usd": 2.0}))
    third = sensor.poll()
    assert len(third) == 1
    assert third[0].payload == {"usd": 2.0}


def test_missing_spend_json_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr("argus.spine.sensors.argus_health.settings.argus_data_dir", str(tmp_path))
    sensor = ArgusHealthSensor()
    assert sensor.poll() == []
