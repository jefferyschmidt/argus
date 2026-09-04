import imaplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from unittest.mock import MagicMock, patch

from argus.email_watcher import EmailWatcher, _EmailSummary, _decode, _decode_part, _plain_text_body, _strip_html
from argus.llm.base import CompletionResult, Tier


def _worker(reply_texts):
    router = MagicMock()
    router.local.complete.side_effect = [
        CompletionResult(text=t, tier=Tier.LOCAL, model="test") for t in reply_texts
    ]
    dispatcher = MagicMock()
    threads = MagicMock()
    threads.open_email_reply.return_value = 42
    return EmailWatcher(router, dispatcher, threads), dispatcher


def test_decode_plain_header():
    assert _decode("Hello there") == "Hello there"


def test_decode_encoded_header():
    # A real MIME-encoded-word header, as email servers actually send them.
    encoded = "=?utf-8?b?SGVsbG8gdGhlcmU=?="  # "Hello there"
    assert _decode(encoded) == "Hello there"


def test_decode_none_is_empty_string():
    assert _decode(None) == ""


def test_decode_unregistered_charset_falls_back_instead_of_raising():
    """Confirmed live: a real batch of 50 Yahoo emails crashed the entire
    fetch on one header declaring 'unknown-8bit' -- a real placeholder
    charset some mail servers use, not an actual registered Python codec,
    so .decode() raised LookupError instead of the more common
    UnicodeDecodeError."""
    with patch("argus.email_watcher.decode_header", return_value=[(b"Hello", "unknown-8bit")]):
        assert _decode("anything") == "Hello"


def test_decode_part_unregistered_charset_falls_back_instead_of_raising():
    part = MIMEText("body text", "plain")
    del part["Content-Transfer-Encoding"]
    part.set_payload(b"raw body bytes")
    part.set_param("charset", "unknown-8bit")
    result = _decode_part(part)
    assert "raw body bytes" in result


def test_plain_text_body_simple_message():
    msg = MIMEText("just a plain message", "plain")
    assert _plain_text_body(msg).strip() == "just a plain message"


def test_plain_text_body_multipart_prefers_plain_over_html():
    msg = MIMEMultipart("alternative")
    msg.attach(MIMEText("<p>html version</p>", "html"))
    msg.attach(MIMEText("plain version", "plain"))
    assert "plain version" in _plain_text_body(msg)


def test_strip_html_removes_tags_and_unescapes_entities():
    html = "<html><body><p>Hello &amp; welcome, <b>friend</b>!</p></body></html>"
    assert _strip_html(html) == "Hello & welcome, friend !"


def test_strip_html_drops_style_block_contents_entirely():
    """Confirmed live: tag-stripping alone left a marketing email's CSS
    visible as plain text ('html { -webkit-text-size-adjust:none; } ...')."""
    html = "<html><head><style>body { color: red; font-family: sans-serif; }</style></head><body>Actual message</body></html>"
    result = _strip_html(html)
    assert "color" not in result
    assert "sans-serif" not in result
    assert "Actual message" in result


def test_plain_text_body_single_part_html_is_stripped():
    """Confirmed live: a real notification email had no text/plain part at
    all, only text/html -- the raw markup was leaking into both the
    on-demand listing tool and the triage classifier's input."""
    msg = MIMEText("<html><body><p>Your app password was used to sign in.</p></body></html>", "html")
    result = _plain_text_body(msg)
    assert "<" not in result
    assert "Your app password was used to sign in." in result


def test_plain_text_body_multipart_html_only_falls_back_stripped():
    msg = MIMEMultipart("alternative")
    msg.attach(MIMEText("<p>only html here</p>", "html"))
    result = _plain_text_body(msg)
    assert "<" not in result
    assert "only html here" in result


def test_ignore_verdict_is_not_important():
    worker, _dispatcher = _worker(["IGNORE"])
    summary = _EmailSummary(account="Gmail", sender="newsletter@example.com", subject="Weekly digest", body="unsubscribe here")
    assert worker._is_important(summary) is False


def test_important_verdict_is_important():
    worker, _dispatcher = _worker(["IMPORTANT"])
    summary = _EmailSummary(account="Yahoo", sender="boss@company.com", subject="Need this today", body="Can you send the report")
    assert worker._is_important(summary) is True


def test_unsure_verdict_escalates_to_full_body_pass():
    worker, _dispatcher = _worker(["UNSURE", "IMPORTANT"])
    summary = _EmailSummary(account="Gmail", sender="a@b.com", subject="?", body="ambiguous short text")
    result = worker._is_important(summary)
    assert result is True
    assert worker.router.local.complete.call_count == 2


# -- submission through the dispatcher (U-C4) --------------------------------

def test_important_mail_submits_a_candidate_and_opens_a_thread():
    worker, dispatcher = _worker(["IMPORTANT"])
    summary = _EmailSummary(account="Yahoo", sender="boss@company.com", subject="Need this today", body="please review")

    worker._submit(summary)

    worker._threads.open_email_reply.assert_called_once_with(sender="boss@company.com", mail_subject="Need this today")
    dispatcher.submit.assert_called_once()
    (candidate,), kwargs = dispatcher.submit.call_args
    assert candidate.kind == "mail.received"
    assert candidate.subject == "boss@company.com"
    assert candidate.thread_id == 42
    assert candidate.base_urgency == 0.55  # important row, Appendix A.2
    assert "boss@company.com" in candidate.text
    assert "Need this today" in candidate.text
    assert kwargs["observation"].kind == "mail.received"


def test_unimportant_mail_submits_a_candidate_without_opening_a_thread():
    worker, dispatcher = _worker(["IGNORE"])
    summary = _EmailSummary(account="Gmail", sender="news@example.com", subject="Weekly digest", body="unsubscribe")

    worker._submit(summary)

    worker._threads.open_email_reply.assert_not_called()
    (candidate,), _kwargs = dispatcher.submit.call_args
    assert candidate.thread_id is None
    assert candidate.base_urgency == 0.10  # not-important row, Appendix A.2


def test_first_check_for_an_account_only_sets_baseline_and_processes_nothing():
    """Confirmed live: on a real long-used inbox, 'unseen' can mean
    thousands of old messages, not new arrivals. The first check must
    never try to triage that backlog."""
    worker, dispatcher = _worker([])

    fake_conn = MagicMock()
    fake_conn.status.return_value = ("OK", [b"INBOX (UIDNEXT 100)"])
    with patch("argus.email_watcher.imaplib.IMAP4_SSL", return_value=fake_conn):
        worker._check_account({"name": "Gmail", "host": "imap.gmail.com"}, "user@gmail.com", "app-password")

    assert worker._baseline_uid["Gmail"] == 100
    fake_conn.uid.assert_not_called()
    dispatcher.submit.assert_not_called()


def test_uid_below_baseline_is_skipped_even_if_unseen():
    worker, dispatcher = _worker(["IMPORTANT"])
    worker._baseline_uid["Gmail"] = 100

    fake_conn = MagicMock()
    fake_conn.uid.return_value = ("OK", [b"42"])  # below baseline -- old backlog mail
    with patch("argus.email_watcher.imaplib.IMAP4_SSL", return_value=fake_conn):
        worker._check_account({"name": "Gmail", "host": "imap.gmail.com"}, "user@gmail.com", "app-password")

    # search was called (uid("search", ...)) but fetch (uid("fetch", ...)) never was
    fetch_calls = [c for c in fake_conn.uid.call_args_list if c.args[0] == "fetch"]
    assert fetch_calls == []
    dispatcher.submit.assert_not_called()


def test_already_triaged_uid_is_not_reprocessed():
    worker, dispatcher = _worker(["IMPORTANT"])
    worker._baseline_uid["Gmail"] = 1
    worker._triaged_uids.add(("Gmail", b"5"))

    fake_conn = MagicMock()
    fake_conn.uid.return_value = ("OK", [b"5"])
    with patch("argus.email_watcher.imaplib.IMAP4_SSL", return_value=fake_conn):
        worker._check_account({"name": "Gmail", "host": "imap.gmail.com"}, "user@gmail.com", "app-password")

    fetch_calls = [c for c in fake_conn.uid.call_args_list if c.args[0] == "fetch"]
    assert fetch_calls == []
    dispatcher.submit.assert_not_called()


def test_new_uid_above_baseline_is_fetched_triaged_and_submitted():
    worker, dispatcher = _worker(["IGNORE"])
    worker._baseline_uid["Gmail"] = 1

    msg = MIMEText("just a newsletter", "plain")
    msg["From"] = "news@example.com"
    msg["Subject"] = "This week's digest"
    raw = msg.as_bytes()

    fake_conn = MagicMock()

    def uid_side_effect(command, *args):
        if command == "search":
            return ("OK", [b"9"])
        if command == "fetch":
            return ("OK", [(b"9 (BODY[])", raw)])
        raise AssertionError(f"unexpected uid command: {command}")
    fake_conn.uid.side_effect = uid_side_effect

    with patch("argus.email_watcher.imaplib.IMAP4_SSL", return_value=fake_conn):
        worker._check_account({"name": "Gmail", "host": "imap.gmail.com"}, "user@gmail.com", "app-password")

    fake_conn.logout.assert_called_once()
    assert ("Gmail", b"9") in worker._triaged_uids
    dispatcher.submit.assert_called_once()  # submitted even though IGNORE -- salience decides now, not this worker
    (candidate,), _kwargs = dispatcher.submit.call_args
    assert candidate.base_urgency == 0.10


# -- IMAP hardening (PRD §19/§20 unit 44) ------------------------------------

def test_connect_passes_the_configured_timeout():
    """44b: a real incident had a bad password tarpitted by Yahoo -- the
    resulting dropped connections hung on the OS default (~21s) with no
    timeout set, masquerading as a network fault."""
    worker, _dispatcher = _worker([])
    fake_conn = MagicMock()
    fake_conn.login.side_effect = imaplib.IMAP4.error("boom")
    with patch("argus.email_watcher.imaplib.IMAP4_SSL", return_value=fake_conn) as mock_ssl, \
         patch("argus.email_watcher.settings.imap_connect_timeout_seconds", 7.0):
        worker._check_account({"name": "Gmail", "host": "imap.gmail.com"}, "user@gmail.com", "app-password")
    mock_ssl.assert_called_once_with("imap.gmail.com", timeout=7.0)


def test_connect_timeout_does_not_count_toward_auth_failure_limit_or_report():
    """44c: a connection timeout is NOT an auth failure -- must not count
    toward the backoff limit or emit argus.credential_failed."""
    worker, _dispatcher = _worker([])
    with patch("argus.email_watcher.imaplib.IMAP4_SSL", side_effect=TimeoutError("timed out")), \
         patch("argus.email_watcher.report_failure") as mock_report, \
         patch("argus.email_watcher.settings.gmail_imap_user", "user@gmail.com"), \
         patch("argus.email_watcher.settings.gmail_imap_app_password", "app-password"), \
         patch("argus.email_watcher.settings.imap_auth_failure_limit", 3):
        for _ in range(5):  # well past the auth-failure limit
            worker.check_now()
    mock_report.assert_not_called()
    assert worker._auth_failures == {}


def test_auth_failure_below_limit_does_not_report():
    worker, _dispatcher = _worker([])
    fake_conn = MagicMock()
    fake_conn.login.side_effect = imaplib.IMAP4.error("AUTHENTICATIONFAILED")
    with patch("argus.email_watcher.imaplib.IMAP4_SSL", return_value=fake_conn), \
         patch("argus.email_watcher.report_failure") as mock_report, \
         patch("argus.email_watcher.settings.imap_auth_failure_limit", 3):
        worker._check_account({"name": "Gmail", "host": "imap.gmail.com"}, "user@gmail.com", "app-password")
        worker._check_account({"name": "Gmail", "host": "imap.gmail.com"}, "user@gmail.com", "app-password")
    mock_report.assert_not_called()
    assert worker._auth_failures["Gmail"] == 2


def test_auth_failure_reaching_limit_reports_exactly_once():
    worker, _dispatcher = _worker([])
    fake_conn = MagicMock()
    fake_conn.login.side_effect = imaplib.IMAP4.error("AUTHENTICATIONFAILED")
    with patch("argus.email_watcher.imaplib.IMAP4_SSL", return_value=fake_conn), \
         patch("argus.email_watcher.report_failure") as mock_report, \
         patch("argus.email_watcher.settings.imap_auth_failure_limit", 3):
        for _ in range(3):
            worker._check_account({"name": "Gmail", "host": "imap.gmail.com"}, "user@gmail.com", "app-password")
    mock_report.assert_called_once_with("argus.credential_failed", "Gmail", {"error": "AUTHENTICATIONFAILED"})


def test_account_stops_being_polled_once_the_limit_is_reached():
    worker, _dispatcher = _worker([])
    fake_conn = MagicMock()
    fake_conn.login.side_effect = imaplib.IMAP4.error("AUTHENTICATIONFAILED")
    with patch("argus.email_watcher.imaplib.IMAP4_SSL", return_value=fake_conn) as mock_ssl, \
         patch("argus.email_watcher.report_failure"), \
         patch("argus.email_watcher.settings.gmail_imap_user", "user@gmail.com"), \
         patch("argus.email_watcher.settings.gmail_imap_app_password", "app-password"), \
         patch("argus.email_watcher.settings.yahoo_imap_user", ""), \
         patch("argus.email_watcher.settings.yahoo_imap_app_password", ""), \
         patch("argus.email_watcher.settings.imap_auth_failure_limit", 3):
        for _ in range(3):
            worker.check_now()
        assert mock_ssl.call_count == 3
        worker.check_now()  # limit reached -- must not even attempt to connect
    assert mock_ssl.call_count == 3


def test_successful_login_after_failures_resets_counter_and_reports_recovery():
    worker, _dispatcher = _worker([])
    worker._auth_failures["Gmail"] = 2
    worker._failed_password["Gmail"] = "app-password"
    fake_conn = MagicMock()
    fake_conn.status.return_value = ("OK", [b"INBOX (UIDNEXT 1)"])
    with patch("argus.email_watcher.imaplib.IMAP4_SSL", return_value=fake_conn), \
         patch("argus.email_watcher.report_recovery") as mock_recovery:
        worker._check_account({"name": "Gmail", "host": "imap.gmail.com"}, "user@gmail.com", "app-password")
    mock_recovery.assert_called_once_with("Gmail")
    assert "Gmail" not in worker._auth_failures


def test_changed_password_resets_counter_and_resumes_polling():
    """44c: 'reset the counter ... when the credential setting changes' --
    a fixed password must not have to wait out the old failure streak."""
    worker, _dispatcher = _worker([])
    fake_conn = MagicMock()
    fake_conn.login.side_effect = imaplib.IMAP4.error("AUTHENTICATIONFAILED")
    with patch("argus.email_watcher.imaplib.IMAP4_SSL", return_value=fake_conn) as mock_ssl, \
         patch("argus.email_watcher.report_failure"), \
         patch("argus.email_watcher.settings.gmail_imap_user", "user@gmail.com"), \
         patch("argus.email_watcher.settings.gmail_imap_app_password", "old-password"), \
         patch("argus.email_watcher.settings.yahoo_imap_user", ""), \
         patch("argus.email_watcher.settings.yahoo_imap_app_password", ""), \
         patch("argus.email_watcher.settings.imap_auth_failure_limit", 2):
        worker.check_now()
        worker.check_now()
    assert mock_ssl.call_count == 2
    assert worker._auth_failures["Gmail"] == 2

    with patch("argus.email_watcher.imaplib.IMAP4_SSL", return_value=fake_conn) as mock_ssl2, \
         patch("argus.email_watcher.report_failure"), \
         patch("argus.email_watcher.settings.gmail_imap_user", "user@gmail.com"), \
         patch("argus.email_watcher.settings.gmail_imap_app_password", "new-password"), \
         patch("argus.email_watcher.settings.yahoo_imap_user", ""), \
         patch("argus.email_watcher.settings.yahoo_imap_app_password", ""), \
         patch("argus.email_watcher.settings.imap_auth_failure_limit", 2):
        worker.check_now()  # different password -- must attempt to connect, not skip
    assert mock_ssl2.call_count == 1
