"""PRD.md §3.4. Thin extraction of email_watcher.EmailWatcher's IMAP fetch
half only -- NOT `_is_important`, which is judgment and moves to Phase C
(salience). Reuses email_watcher's account list and decoding helpers
rather than duplicating them (DRY, per repo conventions).

Backlog-safe the same way the original watcher is: the first poll for an
account only records a baseline (the mailbox's current UIDNEXT) and
reports nothing -- see email_watcher.EmailWatcher's own docstring for why
(triaging thousands of old "unseen" messages on first connect is not what
"tell me about new mail" means). Unlike the original watcher, duplicate
suppression across restarts comes for free from SpineStore's dedupe_key
(P7) rather than an in-process set."""

import email
import imaplib
import logging
import re
import time

from argus.config import settings
from argus.email_watcher import _ACCOUNTS, _decode, _plain_text_body
from argus.spine.observation import Observation
from argus.spine.sensors.argus_health import report_failure
from argus.spine.sensors.base import Sensor

log = logging.getLogger(__name__)


class MailSensor(Sensor):
    name = "mail"

    def __init__(self):
        self.interval_seconds = settings.email_watch_poll_seconds
        self._baseline_uid: dict[str, int] = {}

    def poll(self) -> list[Observation]:
        observations: list[Observation] = []
        for account in _ACCOUNTS:
            user = getattr(settings, account["user_setting"])
            password = getattr(settings, account["password_setting"])
            if not user or not password:
                continue
            observations.extend(self._check_account(account, user, password))
        return observations

    def _get_uidnext(self, conn, mailbox: str = "INBOX") -> int | None:
        status, data = conn.status(mailbox, "(UIDNEXT)")
        if status != "OK" or not data or not data[0]:
            return None
        match = re.search(rb"UIDNEXT (\d+)", data[0])
        return int(match.group(1)) if match else None

    def _check_account(self, account: dict, user: str, password: str) -> list[Observation]:
        try:
            conn = imaplib.IMAP4_SSL(account["host"])
        except Exception:
            log.exception("Mail sensor connect failed for %s", account["name"])
            return []
        try:
            try:
                conn.login(user, password)
            except imaplib.IMAP4.error as e:
                # The motivating case for ArgusHealthSensor existing: a
                # bad app password used to just log.exception and go
                # quiet forever (P7) -- now it's a durable, queryable fact.
                if "AUTHENTICATIONFAILED" in str(e).upper():
                    report_failure("argus.credential_failed", account["name"], {"error": str(e)})
                else:
                    log.exception("Mail sensor login failed for %s", account["name"])
                return []

            conn.select("INBOX", readonly=True)

            if account["name"] not in self._baseline_uid:
                self._baseline_uid[account["name"]] = self._get_uidnext(conn) or 1
                return []

            baseline = self._baseline_uid[account["name"]]
            status, data = conn.uid("search", None, "UNSEEN")
            if status != "OK" or not data or not data[0]:
                return []

            observations = []
            for uid in data[0].split():
                if int(uid) < baseline:
                    continue
                summary = self._fetch_summary(conn, uid)
                if summary is None:
                    continue
                observations.append(Observation(
                    source=self.name, kind="mail.received", ts=time.time(),
                    subject=summary["sender"],
                    payload={"account": account["name"], "subject": summary["subject"], "snippet": summary["body"][:400]},
                    dedupe_key=f"mail:{account['name']}:{uid.decode()}",
                ))
            return observations
        finally:
            try:
                conn.logout()
            except Exception:
                pass

    def _fetch_summary(self, conn, uid: bytes) -> dict | None:
        status, data = conn.uid("fetch", uid, "(BODY.PEEK[])")
        if status != "OK" or not data or not isinstance(data[0], tuple):
            return None
        msg = email.message_from_bytes(data[0][1])
        return {
            "sender": _decode(msg.get("From")),
            "subject": _decode(msg.get("Subject")),
            "body": _plain_text_body(msg),
        }
