from unittest.mock import MagicMock, patch

from argus.tools.email import _send_email


def test_rejects_unknown_account():
    result = _send_email({"account": "outlook", "to": "x@y.com", "subject": "hi", "body": "hi"})
    assert result.startswith("error:")


def test_rejects_unconfigured_account(monkeypatch):
    monkeypatch.setattr("argus.tools.email.settings.gmail_imap_user", "")
    monkeypatch.setattr("argus.tools.email.settings.gmail_imap_app_password", "")
    result = _send_email({"account": "gmail", "to": "x@y.com", "subject": "hi", "body": "hi"})
    assert result.startswith("error:")
    assert "not configured" in result or "configured" in result


def test_sends_via_smtp_with_starttls_and_login(monkeypatch):
    monkeypatch.setattr("argus.tools.email.settings.gmail_imap_user", "me@gmail.com")
    monkeypatch.setattr("argus.tools.email.settings.gmail_imap_app_password", "app-password")

    mock_smtp = MagicMock()
    mock_smtp.__enter__.return_value = mock_smtp
    with patch("argus.tools.email.smtplib.SMTP", return_value=mock_smtp) as mock_smtp_cls:
        result = _send_email({
            "account": "gmail", "to": "friend@example.com",
            "subject": "Re: lunch", "body": "sounds good, noon works",
        })

    mock_smtp_cls.assert_called_once_with("smtp.gmail.com", 587, timeout=15)
    mock_smtp.starttls.assert_called_once()
    mock_smtp.login.assert_called_once_with("me@gmail.com", "app-password")
    mock_smtp.send_message.assert_called_once()
    sent_msg = mock_smtp.send_message.call_args[0][0]
    assert sent_msg["To"] == "friend@example.com"
    assert sent_msg["Subject"] == "Re: lunch"
    assert sent_msg["From"] == "me@gmail.com"
    assert "Sent from gmail" in result
    assert "friend@example.com" in result


def test_smtp_failure_is_reported_as_error_not_raised(monkeypatch):
    monkeypatch.setattr("argus.tools.email.settings.yahoo_imap_user", "me@yahoo.com")
    monkeypatch.setattr("argus.tools.email.settings.yahoo_imap_app_password", "app-password")

    with patch("argus.tools.email.smtplib.SMTP", side_effect=OSError("connection refused")):
        result = _send_email({"account": "yahoo", "to": "x@y.com", "subject": "hi", "body": "hi"})

    assert result.startswith("error:")
    assert "connection refused" in result
