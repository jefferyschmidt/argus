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


def test_authentication_failure_reaching_the_limit_reports_credential_failed(tmp_path, monkeypatch):
    """PRD §19/§20 unit 44c: exactly once, when the consecutive-failure
    count reaches imap_auth_failure_limit -- not on every poll (the
    pre-44c behavior this replaces)."""
    monkeypatch.setattr("argus.spine.sensors.argus_health.settings.argus_data_dir", str(tmp_path))
    _configured_settings(monkeypatch)
    monkeypatch.setattr("argus.spine.sensors.mail.settings.imap_auth_failure_limit", 3)
    fake_conn = MagicMock()
    fake_conn.login.side_effect = imaplib.IMAP4.error("b'AUTHENTICATIONFAILED'")
    with patch("argus.spine.sensors.mail.imaplib.IMAP4_SSL", return_value=fake_conn):
        sensor = MailSensor()
        for _ in range(3):
            observations = sensor.poll()

    assert observations == []

    health = ArgusHealthSensor()
    health_observations = health.poll()
    assert len(health_observations) == 1
    assert health_observations[0].kind == "argus.credential_failed"
    assert health_observations[0].subject == "Gmail"


def test_authentication_failure_below_the_limit_reports_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr("argus.spine.sensors.argus_health.settings.argus_data_dir", str(tmp_path))
    _configured_settings(monkeypatch)
    monkeypatch.setattr("argus.spine.sensors.mail.settings.imap_auth_failure_limit", 3)
    fake_conn = MagicMock()
    fake_conn.login.side_effect = imaplib.IMAP4.error("b'AUTHENTICATIONFAILED'")
    with patch("argus.spine.sensors.mail.imaplib.IMAP4_SSL", return_value=fake_conn):
        sensor = MailSensor()
        sensor.poll()
        sensor.poll()

    health = ArgusHealthSensor()
    assert health.poll() == []


def test_account_stops_being_polled_once_the_limit_is_reached(monkeypatch):
    _configured_settings(monkeypatch)
    monkeypatch.setattr("argus.spine.sensors.mail.settings.imap_auth_failure_limit", 3)
    fake_conn = MagicMock()
    fake_conn.login.side_effect = imaplib.IMAP4.error("b'AUTHENTICATIONFAILED'")
    with patch("argus.spine.sensors.mail.imaplib.IMAP4_SSL", return_value=fake_conn) as mock_ssl:
        sensor = MailSensor()
        for _ in range(3):
            sensor.poll()
        assert mock_ssl.call_count == 3
        sensor.poll()  # limit reached -- must not even attempt to connect

    assert mock_ssl.call_count == 3


def test_successful_login_after_failures_resets_counter_and_reports_recovery(tmp_path, monkeypatch):
    monkeypatch.setattr("argus.spine.sensors.argus_health.settings.argus_data_dir", str(tmp_path))
    _configured_settings(monkeypatch)
    sensor = MailSensor()
    sensor._auth_failures["Gmail"] = 2
    sensor._failed_password["Gmail"] = "app-password"
    fake_conn = MagicMock()
    fake_conn.status.return_value = ("OK", [b"* STATUS INBOX (UIDNEXT 1)"])
    with patch("argus.spine.sensors.mail.imaplib.IMAP4_SSL", return_value=fake_conn):
        sensor.poll()

    assert "Gmail" not in sensor._auth_failures

    health = ArgusHealthSensor()
    health_observations = health.poll()
    assert len(health_observations) == 1
    assert health_observations[0].kind == "argus.credential_recovered"
    assert health_observations[0].subject == "Gmail"


def test_changed_password_resets_the_counter_and_resumes_polling(monkeypatch):
    """44c: 'reset the counter ... when the credential setting changes' --
    a fixed password must not have to wait out the old failure streak."""
    monkeypatch.setattr("argus.spine.sensors.mail._ACCOUNTS", [_ACCOUNT])
    monkeypatch.setattr("argus.spine.sensors.mail.settings.gmail_imap_user", "me@gmail.com")
    monkeypatch.setattr("argus.spine.sensors.mail.settings.gmail_imap_app_password", "old-password")
    monkeypatch.setattr("argus.spine.sensors.mail.settings.imap_auth_failure_limit", 2)
    fake_conn = MagicMock()
    fake_conn.login.side_effect = imaplib.IMAP4.error("AUTHENTICATIONFAILED")
    sensor = MailSensor()
    with patch("argus.spine.sensors.mail.imaplib.IMAP4_SSL", return_value=fake_conn) as mock_ssl:
        sensor.poll()
        sensor.poll()
    assert mock_ssl.call_count == 2
    assert sensor._auth_failures["Gmail"] == 2

    monkeypatch.setattr("argus.spine.sensors.mail.settings.gmail_imap_app_password", "new-password")
    with patch("argus.spine.sensors.mail.imaplib.IMAP4_SSL", return_value=fake_conn) as mock_ssl2:
        sensor.poll()  # different password -- must attempt to connect, not skip
    assert mock_ssl2.call_count == 1


def test_connect_passes_the_configured_timeout(monkeypatch):
    """44b: a real incident had a bad password tarpitted by Yahoo -- the
    resulting dropped connections hung on the OS default (~21s) with no
    timeout set, masquerading as a network fault."""
    _configured_settings(monkeypatch)
    monkeypatch.setattr("argus.spine.sensors.mail.settings.imap_connect_timeout_seconds", 7.0)
    fake_conn = MagicMock()
    fake_conn.login.side_effect = imaplib.IMAP4.error("boom")
    with patch("argus.spine.sensors.mail.imaplib.IMAP4_SSL", return_value=fake_conn) as mock_ssl:
        sensor = MailSensor()
        sensor.poll()
    mock_ssl.assert_called_once_with("imap.gmail.com", timeout=7.0)


def test_connect_timeout_does_not_count_toward_auth_failure_limit_or_report(tmp_path, monkeypatch):
    """44c: a connection timeout is NOT an auth failure -- must not count
    toward the backoff limit or emit argus.credential_failed."""
    monkeypatch.setattr("argus.spine.sensors.argus_health.settings.argus_data_dir", str(tmp_path))
    _configured_settings(monkeypatch)
    monkeypatch.setattr("argus.spine.sensors.mail.settings.imap_auth_failure_limit", 3)
    with patch("argus.spine.sensors.mail.imaplib.IMAP4_SSL", side_effect=TimeoutError("timed out")):
        sensor = MailSensor()
        for _ in range(5):  # well past the auth-failure limit
            sensor.poll()

    assert sensor._auth_failures == {}
    health = ArgusHealthSensor()
    assert health.poll() == []


def test_connect_failure_does_not_raise(monkeypatch):
    _configured_settings(monkeypatch)
    with patch("argus.spine.sensors.mail.imaplib.IMAP4_SSL", side_effect=OSError("network down")):
        sensor = MailSensor()
        assert sensor.poll() == []
