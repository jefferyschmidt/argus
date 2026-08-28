from unittest.mock import MagicMock, patch

from argus.tools.email import _parse_list_unsubscribe, _unsubscribe_from_email


def _raw_message(subject="Weekly Deals", sender="Ring <marketing@ring.com>", list_unsubscribe=None, one_click=False):
    lines = [f"Subject: {subject}", f"From: {sender}"]
    if list_unsubscribe:
        lines.append(f"List-Unsubscribe: {list_unsubscribe}")
    if one_click:
        lines.append("List-Unsubscribe-Post: List-Unsubscribe=One-Click")
    lines.append("")
    lines.append("body")
    return "\r\n".join(lines).encode()


def _mock_imap(raw_messages):
    """raw_messages: list of raw bytes, most-recent-last (matches IMAP's
    ascending-UID search order, like the real _fetch_recent)."""
    conn = MagicMock()
    conn.login.return_value = None
    conn.select.return_value = ("OK", [b""])
    uids = [str(i).encode() for i in range(len(raw_messages))]
    conn.uid.side_effect = None

    def uid_side_effect(command, arg2, *rest):
        if command == "search":
            return "OK", [b" ".join(uids)]
        if command == "fetch":
            idx = int(arg2.decode() if isinstance(arg2, bytes) else arg2)
            return "OK", [(b"1 (BODY[HEADER])", raw_messages[idx])]
        return "OK", [b""]

    conn.uid.side_effect = uid_side_effect
    return conn


def test_parse_list_unsubscribe_extracts_https_and_mailto():
    msg_bytes = _raw_message(list_unsubscribe="<mailto:unsub@ring.com>, <https://ring.com/unsub?u=1>")
    import email
    msg = email.message_from_bytes(msg_bytes)
    info = _parse_list_unsubscribe(msg)
    assert info["https"] == "https://ring.com/unsub?u=1"
    assert info["mailto"] == "mailto:unsub@ring.com"


def test_parse_list_unsubscribe_detects_one_click_post():
    msg_bytes = _raw_message(list_unsubscribe="<https://ring.com/unsub?u=1>", one_click=True)
    import email
    msg = email.message_from_bytes(msg_bytes)
    info = _parse_list_unsubscribe(msg)
    assert info["one_click"] is True


def test_requires_a_sender_or_subject_to_search_by():
    result = _unsubscribe_from_email({})
    assert result.startswith("error:")


def test_no_matching_email_reports_not_found(monkeypatch):
    monkeypatch.setattr("argus.tools.email.settings.gmail_imap_user", "me@gmail.com")
    monkeypatch.setattr("argus.tools.email.settings.gmail_imap_app_password", "app-password")
    monkeypatch.setattr("argus.tools.email.settings.yahoo_imap_user", "")
    monkeypatch.setattr("argus.tools.email.settings.yahoo_imap_app_password", "")

    conn = _mock_imap([_raw_message(sender="Amazon <no-reply@amazon.com>")])
    with patch("argus.tools.email.imaplib.IMAP4_SSL", return_value=conn):
        result = _unsubscribe_from_email({"sender": "ring"})

    assert "Couldn't find" in result


def test_email_with_no_list_unsubscribe_header_reports_manual_fallback(monkeypatch):
    monkeypatch.setattr("argus.tools.email.settings.gmail_imap_user", "me@gmail.com")
    monkeypatch.setattr("argus.tools.email.settings.gmail_imap_app_password", "app-password")
    monkeypatch.setattr("argus.tools.email.settings.yahoo_imap_user", "")
    monkeypatch.setattr("argus.tools.email.settings.yahoo_imap_app_password", "")

    conn = _mock_imap([_raw_message(sender="Ring <marketing@ring.com>")])
    with patch("argus.tools.email.imaplib.IMAP4_SSL", return_value=conn):
        result = _unsubscribe_from_email({"sender": "ring"})

    assert "doesn't have a machine-readable unsubscribe link" in result


def test_https_one_click_unsubscribe_posts_and_reports_success(monkeypatch):
    monkeypatch.setattr("argus.tools.email.settings.gmail_imap_user", "me@gmail.com")
    monkeypatch.setattr("argus.tools.email.settings.gmail_imap_app_password", "app-password")
    monkeypatch.setattr("argus.tools.email.settings.yahoo_imap_user", "")
    monkeypatch.setattr("argus.tools.email.settings.yahoo_imap_app_password", "")

    conn = _mock_imap([_raw_message(
        sender="Ring <marketing@ring.com>",
        list_unsubscribe="<https://ring.com/unsub?u=1>",
        one_click=True,
    )])
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.__enter__.return_value = mock_response
    with patch("argus.tools.email.imaplib.IMAP4_SSL", return_value=conn), \
         patch("argus.tools.email.urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
        result = _unsubscribe_from_email({"sender": "ring"})

    assert "Unsubscribed" in result
    req = mock_urlopen.call_args[0][0]
    assert req.full_url == "https://ring.com/unsub?u=1"
    assert req.get_method() == "POST"


def test_mailto_fallback_sends_unsubscribe_email(monkeypatch):
    monkeypatch.setattr("argus.tools.email.settings.gmail_imap_user", "me@gmail.com")
    monkeypatch.setattr("argus.tools.email.settings.gmail_imap_app_password", "app-password")
    monkeypatch.setattr("argus.tools.email.settings.yahoo_imap_user", "")
    monkeypatch.setattr("argus.tools.email.settings.yahoo_imap_app_password", "")

    conn = _mock_imap([_raw_message(
        sender="Ring <marketing@ring.com>",
        list_unsubscribe="<mailto:unsub@ring.com>",
    )])
    mock_smtp = MagicMock()
    mock_smtp.__enter__.return_value = mock_smtp
    with patch("argus.tools.email.imaplib.IMAP4_SSL", return_value=conn), \
         patch("argus.tools.email.smtplib.SMTP", return_value=mock_smtp):
        result = _unsubscribe_from_email({"sender": "ring"})

    assert "Unsubscribed" in result
    assert "unsub@ring.com" in result
    mock_smtp.send_message.assert_called_once()


def test_broken_unsubscribe_link_is_reported_as_error(monkeypatch):
    monkeypatch.setattr("argus.tools.email.settings.gmail_imap_user", "me@gmail.com")
    monkeypatch.setattr("argus.tools.email.settings.gmail_imap_app_password", "app-password")
    monkeypatch.setattr("argus.tools.email.settings.yahoo_imap_user", "")
    monkeypatch.setattr("argus.tools.email.settings.yahoo_imap_app_password", "")

    conn = _mock_imap([_raw_message(
        sender="Ring <marketing@ring.com>",
        list_unsubscribe="<https://ring.com/unsub?u=1>",
    )])
    with patch("argus.tools.email.imaplib.IMAP4_SSL", return_value=conn), \
         patch("argus.tools.email.urllib.request.urlopen", side_effect=OSError("timed out")):
        result = _unsubscribe_from_email({"sender": "ring"})

    assert result.startswith("error:")
    assert "timed out" in result
