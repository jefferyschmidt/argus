import threading
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from unittest.mock import MagicMock, patch

from argus.email_watcher import EmailWatcher, _EmailSummary, _decode, _plain_text_body
from argus.llm.base import CompletionResult, Tier


def _worker(reply_texts):
    orchestrator = MagicMock()
    orchestrator.router.local.complete.side_effect = [
        CompletionResult(text=t, tier=Tier.LOCAL, model="test") for t in reply_texts
    ]
    speak_fn = MagicMock()
    lock = threading.Lock()
    return EmailWatcher(orchestrator, speak_fn, lock), speak_fn


def test_decode_plain_header():
    assert _decode("Hello there") == "Hello there"


def test_decode_encoded_header():
    # A real MIME-encoded-word header, as email servers actually send them.
    encoded = "=?utf-8?b?SGVsbG8gdGhlcmU=?="  # "Hello there"
    assert _decode(encoded) == "Hello there"


def test_decode_none_is_empty_string():
    assert _decode(None) == ""


def test_plain_text_body_simple_message():
    msg = MIMEText("just a plain message", "plain")
    assert _plain_text_body(msg).strip() == "just a plain message"


def test_plain_text_body_multipart_prefers_plain_over_html():
    msg = MIMEMultipart("alternative")
    msg.attach(MIMEText("<p>html version</p>", "html"))
    msg.attach(MIMEText("plain version", "plain"))
    assert "plain version" in _plain_text_body(msg)


def test_ignore_verdict_never_delivers():
    worker, speak_fn = _worker(["IGNORE"])
    summary = _EmailSummary(account="Gmail", sender="newsletter@example.com", subject="Weekly digest", body="unsubscribe here")
    assert worker._is_important(summary) is False
    speak_fn.assert_not_called()


def test_important_verdict_delivers_with_sender_and_subject():
    worker, speak_fn = _worker(["IMPORTANT"])
    summary = _EmailSummary(account="Yahoo", sender="boss@company.com", subject="Need this today", body="Can you send the report")
    assert worker._is_important(summary) is True
    worker._deliver(summary)
    speak_fn.assert_called_once()
    (spoken_text,) = speak_fn.call_args[0]
    assert "boss@company.com" in spoken_text
    assert "Need this today" in spoken_text


def test_unsure_verdict_escalates_to_full_body_pass():
    worker, speak_fn = _worker(["UNSURE", "IMPORTANT"])
    summary = _EmailSummary(account="Gmail", sender="a@b.com", subject="?", body="ambiguous short text")
    result = worker._is_important(summary)
    assert result is True
    assert worker.orchestrator.router.local.complete.call_count == 2


def test_deliver_skips_when_interaction_lock_held():
    orchestrator = MagicMock()
    speak_fn = MagicMock()
    lock = threading.Lock()
    lock.acquire()
    worker = EmailWatcher(orchestrator, speak_fn, lock)
    summary = _EmailSummary(account="Gmail", sender="x@y.com", subject="s", body="b")

    worker._deliver(summary)

    speak_fn.assert_not_called()


def test_already_announced_uid_is_not_reprocessed():
    worker, speak_fn = _worker(["IMPORTANT"])
    worker._announced_uids.add(("Gmail", b"5"))

    fake_conn = MagicMock()
    fake_conn.search.return_value = ("OK", [b"5"])
    with patch("argus.email_watcher.imaplib.IMAP4_SSL", return_value=fake_conn):
        worker._check_account({"name": "Gmail", "host": "imap.gmail.com"}, "user@gmail.com", "app-password")

    fake_conn.fetch.assert_not_called()
    speak_fn.assert_not_called()


def test_new_uid_is_fetched_and_triaged():
    worker, speak_fn = _worker(["IGNORE"])

    msg = MIMEText("just a newsletter", "plain")
    msg["From"] = "news@example.com"
    msg["Subject"] = "This week's digest"
    raw = msg.as_bytes()

    fake_conn = MagicMock()
    fake_conn.search.return_value = ("OK", [b"9"])
    fake_conn.fetch.return_value = ("OK", [(b"9 (BODY[])", raw)])
    with patch("argus.email_watcher.imaplib.IMAP4_SSL", return_value=fake_conn):
        worker._check_account({"name": "Gmail", "host": "imap.gmail.com"}, "user@gmail.com", "app-password")

    fake_conn.fetch.assert_called_once()
    fake_conn.logout.assert_called_once()
    assert ("Gmail", b"9") in worker._announced_uids
    speak_fn.assert_not_called()  # IGNORE verdict
