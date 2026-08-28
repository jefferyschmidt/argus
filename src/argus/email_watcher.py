import email
import imaplib
import logging
import time
from dataclasses import dataclass
from email.header import decode_header

from argus.config import settings
from argus.ui import commands as ui_commands
from argus.ui import events as ui_events

log = logging.getLogger(__name__)

# IMAP, not SMTP (that only sends) or POP3 (downloads-and-removes, which
# would fight with your normal mail client over the same inbox's state).
# IMAP's "search UNSEEN" plus BODY.PEEK (never marks a message read) lets
# this watch alongside a real mail client without disturbing it.
_ACCOUNTS = [
    {"name": "Gmail", "host": "imap.gmail.com", "user_setting": "gmail_imap_user", "password_setting": "gmail_imap_app_password"},
    {"name": "Yahoo", "host": "imap.mail.yahoo.com", "user_setting": "yahoo_imap_user", "password_setting": "yahoo_imap_app_password"},
]

_SNIPPET_CHARS = 400
_FULL_BODY_CHARS = 4000

_TRIAGE_PROMPT = """You're triaging one email to decide if it's worth interrupting the user
for. Most email isn't -- newsletters, receipts, automated notifications,
marketing. Only flag something that looks like it genuinely needs the
user's attention soon (a real person asking something, a time-sensitive
matter, something that sounds important).

Account: {account}
From: {sender}
Subject: {subject}
{body_label}: {body}

Reply with exactly one word:
IMPORTANT -- clearly worth telling the user about now
IGNORE -- clearly doesn't need attention
UNSURE -- can't tell from this much, would need to see more of the email"""


def _decode(value: str | None) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    out = []
    for text, charset in parts:
        if isinstance(text, bytes):
            out.append(text.decode(charset or "utf-8", errors="replace"))
        else:
            out.append(text)
    return "".join(out).strip()


def _plain_text_body(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get("Content-Disposition"):
                try:
                    return part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="replace")
                except Exception:
                    continue
        return ""
    try:
        return msg.get_payload(decode=True).decode(msg.get_content_charset() or "utf-8", errors="replace")
    except Exception:
        return ""


@dataclass
class _EmailSummary:
    account: str
    sender: str
    subject: str
    body: str


class EmailWatcher:
    """Polls Gmail/Yahoo over IMAP for new mail and, when something looks
    genuinely worth interrupting for, announces it -- same non-blocking
    delivery pattern as reminders and proactive context awareness.

    Privacy-conscious triage by design: the first pass to the classifier
    only ever gets sender + subject + a short snippet, not the full email.
    Only escalates to the full body (still capped, not the whole message
    verbatim) when that first pass comes back UNSURE, or already looks
    IMPORTANT and a second look would sharpen the confirmation -- most
    email never leaves the snippet stage.

    Read-state safe: uses BODY.PEEK (never IMAP-marks a message \\Seen),
    so this never disturbs what your regular mail client shows as
    unread. Tracks which UIDs it has already announced in memory (not
    persisted) so a restart re-triages recent unseen mail once, not the
    same message forever, but also won't remember across restarts --
    acceptable for a first pass; not a guarantee against ever repeating."""

    def __init__(self, orchestrator, speak_fn, interaction_lock):
        self.orchestrator = orchestrator
        self._speak_fn = speak_fn
        self._interaction_lock = interaction_lock
        self._announced_uids: set[tuple[str, bytes]] = set()

    def run(self) -> None:
        while True:
            time.sleep(settings.email_watch_poll_seconds)
            if not ui_commands.is_email_watch_enabled():
                continue
            self.check_now()

    def check_now(self) -> None:
        for account in _ACCOUNTS:
            user = getattr(settings, account["user_setting"])
            password = getattr(settings, account["password_setting"])
            if not user or not password:
                continue
            try:
                self._check_account(account, user, password)
            except Exception:
                log.exception("Email check failed for %s", account["name"])

    def _check_account(self, account: dict, user: str, password: str) -> None:
        conn = imaplib.IMAP4_SSL(account["host"])
        try:
            conn.login(user, password)
            conn.select("INBOX", readonly=True)
            status, data = conn.search(None, "UNSEEN")
            if status != "OK":
                return
            for uid in data[0].split():
                key = (account["name"], uid)
                if key in self._announced_uids:
                    continue
                self._announced_uids.add(key)
                summary = self._fetch_summary(conn, account["name"], uid)
                if summary is None:
                    continue
                if self._is_important(summary):
                    self._deliver(summary)
        finally:
            try:
                conn.logout()
            except Exception:
                pass

    def _fetch_summary(self, conn, account_name: str, uid: bytes) -> "_EmailSummary | None":
        status, data = conn.fetch(uid, "(BODY.PEEK[])")
        if status != "OK" or not data or not isinstance(data[0], tuple):
            return None
        msg = email.message_from_bytes(data[0][1])
        return _EmailSummary(
            account=account_name,
            sender=_decode(msg.get("From")),
            subject=_decode(msg.get("Subject")),
            body=_plain_text_body(msg),
        )

    def _is_important(self, summary: _EmailSummary) -> bool:
        verdict = self._ask_triage(summary, summary.body[:_SNIPPET_CHARS], "Snippet")
        if verdict == "UNSURE":
            verdict = self._ask_triage(summary, summary.body[:_FULL_BODY_CHARS], "Full body")
        return verdict == "IMPORTANT"

    def _ask_triage(self, summary: _EmailSummary, body: str, body_label: str) -> str:
        from argus.llm.base import Message

        prompt = _TRIAGE_PROMPT.format(
            account=summary.account, sender=summary.sender, subject=summary.subject,
            body_label=body_label, body=body or "(empty)",
        )
        try:
            result = self.orchestrator.router.local.complete([Message(role="user", content=prompt)])
        except Exception:
            log.exception("Email triage call failed")
            return "IGNORE"
        text = result.text.strip().upper()
        if text.startswith("IMPORTANT"):
            return "IMPORTANT"
        if text.startswith("UNSURE"):
            return "UNSURE"
        return "IGNORE"

    def _deliver(self, summary: _EmailSummary) -> None:
        text = f"You've got an email from {summary.sender} about \"{summary.subject}\" that looks like it needs attention."
        if not self._interaction_lock.acquire(blocking=False):
            return
        try:
            ui_events.publish({"type": "transcript", "role": "argus", "text": text})
            ui_events.publish({"type": "caption", "text": text})
            self._speak_fn(text)
        finally:
            self._interaction_lock.release()
