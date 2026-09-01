import imaplib
from unittest.mock import MagicMock, patch

import pytest

from argus.spine.sensors import argus_health
from argus.spine.sensors.argus_health import ArgusHealthSensor
from argus.spine.sensors.mail import MailSensor

_ACCOUNT = {"name": "Gmail", "host": "imap.gmail.com", "user_setting": "gmail_imap_user", "password_setting": "gmail_imap_app_password"}


@pytest.fixture(autouse=True)
def _clear_health_queue():
    argus_health._queue.clear()
    yield
    argus_health._queue.clear()


def _configured_settings(monkeypatch):
    monkeypatch.setattr("argus.spine.sensors.mail._ACCOUNTS", [_ACCOUNT])
    monkeypatch.setattr("argus.spine.sensors.mail.settings.gmail_imap_user", "me@gmail.com")
    monkeypatch.setattr("argus.spine.sensors.mail.settings.gmail_imap_app_password", "app-password")


def test_unconfigured_account_is_skipped(monkeypatch):
    monkeypatch.setattr("argus.spine.sensors.mail._ACCOUNTS", [_ACCOUNT])
    monkeypatch.setattr("argus.spine.sensors.mail.settings.gmail_imap_user", "")
    monkeypatch.setattr("argus.spine.sensors.mail.settings.gmail_imap_app_password", "")
    sensor = MailSensor()
    assert sensor.poll() == []


def test_first_poll_only_sets_baseline_and_reports_nothing(monkeypatch):
    _configured_settings(monkeypatch)
    fake_conn = MagicMock()
    fake_conn.status.return_value = ("OK", [b"* STATUS INBOX (UIDNEXT 42)"])
    with patch("argus.spine.sensors.mail.imaplib.IMAP4_SSL", return_value=fake_conn):
        sensor = MailSensor()
        observations = sensor.poll()

    assert observations == []
    assert sensor._baseline_uid["Gmail"] == 42


def test_new_mail_above_baseline_emits_mail_received_with_dedupe_key(monkeypatch):
    _configured_settings(monkeypatch)
    fake_conn = MagicMock()
    fake_conn.status.return_value = ("OK", [b"* STATUS INBOX (UIDNEXT 42)"])
    fake_conn.uid.side_effect = [
        ("OK", [b"50"]),  # search UNSEEN
        ("OK", [(b"1 (UID 50 BODY[])", b"From: sender@x.com\r\nSubject: Hi\r\n\r\nbody text")]),  # fetch
    ]
    with patch("argus.spine.sensors.mail.imaplib.IMAP4_SSL", return_value=fake_conn):
        sensor = MailSensor()
        sensor.poll()  # baseline
        observations = sensor.poll()

    assert len(observations) == 1
    obs = observations[0]
    assert obs.kind == "mail.received"
    assert obs.subject == "sender@x.com"
    assert obs.payload["subject"] == "Hi"
    assert obs.dedupe_key == "mail:Gmail:50"


def test_mail_below_baseline_is_ignored(monkeypatch):
    _configured_settings(monkeypatch)
    fake_conn = MagicMock()
    fake_conn.status.return_value = ("OK", [b"* STATUS INBOX (UIDNEXT 42)"])
    fake_conn.uid.return_value = ("OK", [b"10"])  # below baseline of 42
    with patch("argus.spine.sensors.mail.imaplib.IMAP4_SSL", return_value=fake_conn):
        sensor = MailSensor()
        sensor.poll()
        observations = sensor.poll()

    assert observations == []


def test_authentication_failure_reports_credential_failed(tmp_path, monkeypatch):
    monkeypatch.setattr("argus.spine.sensors.argus_health.settings.argus_data_dir", str(tmp_path))
    _configured_settings(monkeypatch)
    fake_conn = MagicMock()
    fake_conn.login.side_effect = imaplib.IMAP4.error("b'AUTHENTICATIONFAILED'")
    with patch("argus.spine.sensors.mail.imaplib.IMAP4_SSL", return_value=fake_conn):
        sensor = MailSensor()
        observations = sensor.poll()

    assert observations == []

    health = ArgusHealthSensor()
    health_observations = health.poll()
    assert len(health_observations) == 1
    assert health_observations[0].kind == "argus.credential_failed"
    assert health_observations[0].subject == "Gmail"


def test_connect_failure_does_not_raise(monkeypatch):
    _configured_settings(monkeypatch)
    with patch("argus.spine.sensors.mail.imaplib.IMAP4_SSL", side_effect=OSError("network down")):
        sensor = MailSensor()
        assert sensor.poll() == []
