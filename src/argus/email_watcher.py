import email
import email.message
import imaplib
import logging
import re
import time
from dataclasses import dataclass
from email.header import decode_header

from argus.config import settings
from argus.salience.scoring import Candidate, base_urgency_for
from argus.spine.observation import Observation
from argus.spine.sensors.argus_health import report_failure, report_recovery
from argus.ui import commands as ui_commands

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
    """Confirmed live: a real batch of 50 Yahoo emails crashed the whole
    fetch on a single header ("unknown-8bit" -- a real placeholder charset
    some mail servers declare, not an actual registered Python codec, so
    .decode() raises LookupError rather than the more common
    UnicodeDecodeError). One bad header shouldn't take down the other 49
    good ones -- fall back to utf-8-with-replace, which never raises."""
    if not value:
        return ""
    parts = decode_header(value)
    out = []
    for text, charset in parts:
        if isinstance(text, bytes):
            try:
                out.append(text.decode(charset or "utf-8", errors="replace"))
            except (LookupError, UnicodeDecodeError):
                out.append(text.decode("utf-8", errors="replace"))
        else:
            out.append(text)
    return "".join(out).strip()


_HTML_STYLE_OR_SCRIPT = re.compile(r"<(style|script)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_HTML_TAG = re.compile(r"<[^>]+>")
_HTML_WHITESPACE_RUN = re.compile(r"[ \t]+")
_HTML_BLANK_LINES = re.compile(r"\n\s*\n+")


def _strip_html(html: str) -> str:
    """Confirmed live: a real single-part-HTML email (no text/plain part
    at all -- common for marketing/notification mail) was coming through
    as raw markup, both in the on-demand listing tool's output and in what
    the triage classifier sees. Not a real HTML renderer, just enough to
    make the text readable -- drop style/script blocks ENTIRELY first
    (plain tag-stripping alone leaves their inner CSS/JS behind as visible
    "content" -- also confirmed live, a marketing email's stylesheet
    showed up verbatim in the snippet), then strip tags, collapse the
    whitespace that leaves behind, and unescape the handful of entities
    worth bothering with."""
    text = _HTML_STYLE_OR_SCRIPT.sub(" ", html)
    text = _HTML_TAG.sub(" ", text)
    for entity, replacement in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'")):
        text = text.replace(entity, replacement)
    text = _HTML_WHITESPACE_RUN.sub(" ", text)
    text = _HTML_BLANK_LINES.sub("\n", text)
    return text.strip()


def _decode_part(part: email.message.Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    try:
        raw = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    except (LookupError, UnicodeDecodeError):
        # Same "unknown-8bit"-style unregistered-charset issue as _decode()
        # above -- fall back rather than losing the whole body.
        raw = payload.decode("utf-8", errors="replace")
    return _strip_html(raw) if part.get_content_type() == "text/html" else raw


def _plain_text_body(msg: email.message.Message) -> str:
    if msg.is_multipart():
        html_fallback = ""
        for part in msg.walk():
            if part.get("Content-Disposition"):
                continue
            if part.get_content_type() == "text/plain":
                text = _decode_part(part)
                if text:
                    return text
            elif part.get_content_type() == "text/html" and not html_fallback:
                html_fallback = _decode_part(part)
        return html_fallback  # only used if there was no text/plain part at all
    return _decode_part(msg)


@dataclass
class _EmailSummary:
    account: str
    sender: str
    subject: str
    body: str


class EmailWatcher:
    """Polls Gmail/Yahoo over IMAP for new mail, triages it, and submits
    it to salience -- same shape as the other detect-then-submit workers
    since U-C4 (PRD §5/§7 build order).

    Privacy-conscious triage by design: the first pass to the classifier
    only ever gets sender + subject + a short snippet, not the full email.
    Only escalates to the full body (still capped, not the whole message
    verbatim) when that first pass comes back UNSURE, or already looks
    IMPORTANT and a second look would sharpen the confirmation -- most
    email never leaves the snippet stage. The triage verdict now feeds
    Candidate.base_urgency (Appendix A.2's two mail.received rows) rather
    than gating delivery itself -- that decision belongs to salience.
    U-C4 also retired this worker's own _pending_delivery retry queue:
    SalienceDispatcher/HeldQueue already guarantee nothing important is
    silently dropped if Argus is mid-conversation, so a second, redundant
    retry mechanism here was no longer pulling its weight.

    Read-state safe: uses BODY.PEEK (never IMAP-marks a message \\Seen),
    so this never disturbs what your regular mail client shows as
    unread. Uses UID SEARCH/FETCH throughout, not plain sequence-number
    search/fetch -- sequence numbers shift whenever the mailbox changes
    (a message deleted elsewhere renumbers everything after it), which
    would silently corrupt the announced-message dedup below; UIDs are
    stable for the mailbox's lifetime.

    Backlog-safe: the first check for any account only records a baseline
    (the mailbox's current UIDNEXT) and processes nothing -- confirmed
    live that "unseen" on a real long-used inbox can mean thousands of
    old messages, not new arrivals, and triaging that backlog on first
    connect would be slow, expensive, and not what "tell me about new
    mail" means. Only mail that arrives at or after that baseline is ever
    considered. Also tracks which UIDs it has already triaged in memory
    (not persisted) so a restart re-triages recent unseen mail once, not
    the same message forever, but also won't remember across restarts --
    acceptable for a first pass; not a guarantee against ever repeating."""

    def __init__(self, router, dispatcher, threads):
        self.router = router
        self._dispatcher = dispatcher
        self._threads = threads
        self._triaged_uids: set[tuple[str, bytes]] = set()
        self._baseline_uid: dict[str, int] = {}
        # PRD §19/§20 unit 44c: consecutive-AUTHENTICATIONFAILED count and
        # the password that produced it, per account -- keyed on the
        # password so a changed credential (the user fixing it) is
        # detected and resets polling immediately rather than waiting out
        # the old failure streak.
        self._auth_failures: dict[str, int] = {}
        self._failed_password: dict[str, str] = {}

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
            name = account["name"]
            if self._failed_password.get(name) != password:
                self._auth_failures.pop(name, None)
                self._failed_password.pop(name, None)
            elif self._auth_failures.get(name, 0) >= settings.imap_auth_failure_limit:
                # Stopped polling this account -- argus.credential_failed
                # was already reported exactly once when the limit was
                # first reached, in _check_account below.
                continue
            try:
                self._check_account(account, user, password)
            except Exception:
                log.exception("Email check failed for %s", name)

    def _get_uidnext(self, conn, mailbox: str = "INBOX") -> int | None:
        status, data = conn.status(mailbox, "(UIDNEXT)")
        if status != "OK" or not data or not data[0]:
            return None
        match = re.search(rb"UIDNEXT (\d+)", data[0])
        return int(match.group(1)) if match else None

    def _check_account(self, account: dict, user: str, password: str) -> None:
        name = account["name"]
        # 44b: a real incident had a bad password tarpitted by Yahoo -- the
        # resulting dropped connections hung on the OS default (~21s) with
        # no timeout set, masquerading as a network fault. A connect/
        # timeout failure here is NOT an auth failure -- it propagates to
        # check_now()'s own except Exception without touching the counter
        # below or emitting argus.credential_failed.
        conn = imaplib.IMAP4_SSL(account["host"], timeout=settings.imap_connect_timeout_seconds)
        try:
            try:
                conn.login(user, password)
            except imaplib.IMAP4.error as e:
                # The motivating case for ArgusHealthSensor existing: a
                # bad app password used to just log.exception and go
                # quiet forever -- now it's a durable, queryable fact.
                if "AUTHENTICATIONFAILED" in str(e).upper():
                    failures = self._auth_failures.get(name, 0) + 1
                    self._auth_failures[name] = failures
                    self._failed_password[name] = password
                    if failures == settings.imap_auth_failure_limit:
                        # Exactly once -- not one report per poll.
                        report_failure("argus.credential_failed", name, {"error": str(e)})
                else:
                    # Some other IMAP4 error at login (not
                    # AUTHENTICATIONFAILED) -- doesn't count toward the
                    # auth-failure limit either.
                    log.exception("Email watcher login failed for %s", name)
                return

            if self._auth_failures.pop(name, None):
                self._failed_password.pop(name, None)
                report_recovery(name)

            conn.select("INBOX", readonly=True)

            if account["name"] not in self._baseline_uid:
                self._baseline_uid[account["name"]] = self._get_uidnext(conn) or 1
                log.info("Email watcher baseline set for %s (UID >= %s)", account["name"], self._baseline_uid[account["name"]])
                return

            baseline = self._baseline_uid[account["name"]]
            status, data = conn.uid("search", None, "UNSEEN")
            if status != "OK" or not data or not data[0]:
                return
            for uid in data[0].split():
                if int(uid) < baseline:
                    continue
                key = (account["name"], uid)
                if key in self._triaged_uids:
                    continue
                summary = self._fetch_summary(conn, account["name"], uid)
                if summary is None:
                    continue
                # Marked before submission, deliberately: triage costs one
                # or two LLM calls, so it must never re-run for this UID.
                # Unlike before U-C4, there's no separate retry queue here
                # if Argus is mid-conversation -- SalienceDispatcher/
                # HeldQueue already guarantee the finding isn't lost.
                self._triaged_uids.add(key)
                self._submit(summary)
        finally:
            try:
                conn.logout()
            except Exception:
                pass

    def _fetch_summary(self, conn, account_name: str, uid: bytes) -> "_EmailSummary | None":
        status, data = conn.uid("fetch", uid, "(BODY.PEEK[])")
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
            result = self.router.local.complete([Message(role="user", content=prompt)])
        except Exception:
            log.exception("Email triage call failed")
            return "IGNORE"
        text = result.text.strip().upper()
        if text.startswith("IMPORTANT"):
            return "IMPORTANT"
        if text.startswith("UNSURE"):
            return "UNSURE"
        return "IGNORE"

    def _submit(self, summary: _EmailSummary) -> None:
        """Triage still happens here (feeds Candidate.base_urgency,
        Appendix A.2's two mail.received rows) -- whether to actually
        interrupt about it is SalienceEngine's call now, not this
        worker's (U-C4)."""
        important = self._is_important(summary)
        thread_id = None
        if important:
            thread_id = self._threads.open_email_reply(sender=summary.sender, mail_subject=summary.subject)

        text = f"You've got an email from {summary.sender} about \"{summary.subject}\" that looks like it needs attention."
        candidate = Candidate(
            observation_id=None, kind="mail.received", subject=summary.sender, text=text,
            base_urgency=base_urgency_for("mail.received", important=important), thread_id=thread_id,
        )
        observation = Observation(
            source="mail_watcher", kind="mail.received", ts=time.time(), subject=summary.sender,
            payload={"account": summary.account, "subject": summary.subject},
        )
        self._dispatcher.submit(candidate, observation=observation)
