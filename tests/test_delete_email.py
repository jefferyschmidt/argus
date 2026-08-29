from unittest.mock import MagicMock, patch

from argus.tools.email import _delete_email


def _raw_message(subject="Weekly Deals", sender="Ring <marketing@ring.com>"):
    lines = [f"Subject: {subject}", f"From: {sender}", "", "body"]
    return "\r\n".join(lines).encode()


def _mock_imap(raw_messages):
    conn = MagicMock()
    conn.login.return_value = None
    conn.select.return_value = ("OK", [b""])
    uids = [str(i).encode() for i in range(len(raw_messages))]

    def uid_side_effect(command, arg2, *rest):
        if command == "search":
            return "OK", [b" ".join(uids)]
        if command == "fetch":
            idx = int(arg2.decode() if isinstance(arg2, bytes) else arg2)
            return "OK", [(b"1 (BODY[HEADER])", raw_messages[idx])]
        if command == "store":
            return "OK", [b""]
        return "OK", [b""]

    conn.uid.side_effect = uid_side_effect
    return conn


def test_requires_a_sender_or_subject_to_search_by():
    result = _delete_email({})
    assert result.startswith("error:")


def test_no_matching_email_reports_not_found(monkeypatch):
    monkeypatch.setattr("argus.tools.email.settings.gmail_imap_user", "me@gmail.com")
    monkeypatch.setattr("argus.tools.email.settings.gmail_imap_app_password", "app-password")
    monkeypatch.setattr("argus.tools.email.settings.yahoo_imap_user", "")
    monkeypatch.setattr("argus.tools.email.settings.yahoo_imap_app_password", "")

    conn = _mock_imap([_raw_message(sender="Amazon <no-reply@amazon.com>")])
    with patch("argus.tools.email.imaplib.IMAP4_SSL", return_value=conn):
        result = _delete_email({"sender": "ring"})

    assert "Couldn't find" in result


def test_matching_email_is_marked_deleted_and_expunged(monkeypatch):
    monkeypatch.setattr("argus.tools.email.settings.gmail_imap_user", "me@gmail.com")
    monkeypatch.setattr("argus.tools.email.settings.gmail_imap_app_password", "app-password")
    monkeypatch.setattr("argus.tools.email.settings.yahoo_imap_user", "")
    monkeypatch.setattr("argus.tools.email.settings.yahoo_imap_app_password", "")

    conn = _mock_imap([_raw_message(sender="Ring <marketing@ring.com>", subject="Weekly Deals")])
    with patch("argus.tools.email.imaplib.IMAP4_SSL", return_value=conn):
        result = _delete_email({"sender": "ring"})

    assert "Deleted" in result
    assert "Weekly Deals" in result
    conn.expunge.assert_called_once()
    store_call = [c for c in conn.uid.call_args_list if c.args[0] == "store"][0]
    assert store_call.args[3] == "\\Deleted"


def test_select_is_not_readonly_since_deleting_requires_write_access(monkeypatch):
    monkeypatch.setattr("argus.tools.email.settings.gmail_imap_user", "me@gmail.com")
    monkeypatch.setattr("argus.tools.email.settings.gmail_imap_app_password", "app-password")
    monkeypatch.setattr("argus.tools.email.settings.yahoo_imap_user", "")
    monkeypatch.setattr("argus.tools.email.settings.yahoo_imap_app_password", "")

    conn = _mock_imap([_raw_message(sender="Ring <marketing@ring.com>")])
    with patch("argus.tools.email.imaplib.IMAP4_SSL", return_value=conn):
        _delete_email({"sender": "ring"})

    conn.select.assert_called_once_with("INBOX")
