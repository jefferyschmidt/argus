import email
import imaplib
import smtplib
from email.mime.text import MIMEText

from argus.config import settings
from argus.email_watcher import _ACCOUNTS, _decode, _plain_text_body
from argus.tools.base import PermissionTier, Tool

_LIST_SNIPPET_CHARS = 200

# Same host pattern as _ACCOUNTS in email_watcher.py, but SMTP (send) not
# IMAP (read) -- same app-password credentials work for both, so sending
# needed no new setup beyond what email reading already required.
_SMTP_ACCOUNTS = {
    "gmail": {"host": "smtp.gmail.com", "port": 587, "user_setting": "gmail_imap_user", "password_setting": "gmail_imap_app_password"},
    "yahoo": {"host": "smtp.mail.yahoo.com", "port": 587, "user_setting": "yahoo_imap_user", "password_setting": "yahoo_imap_app_password"},
}


def _list_recent_emails(args: dict) -> str:
    """On-demand "what's in my inbox" -- distinct from EmailWatcher's
    background proactive triage. Without this, a request like "check my
    Yahoo email and tell me my recent emails" had no real tool to answer
    it with, and fell back to trying to browser-navigate to Yahoo Mail
    manually (confirmed live) -- IMAP is already wired up for exactly
    this, it just wasn't exposed as something the model could call."""
    limit = args.get("limit") or 5
    account_filter = (args.get("account") or "").strip().lower()

    results = []
    errors = []
    for account in _ACCOUNTS:
        if account_filter and account_filter != account["name"].lower():
            continue
        user = getattr(settings, account["user_setting"])
        password = getattr(settings, account["password_setting"])
        if not user or not password:
            continue
        try:
            results.extend(_fetch_recent(account, user, password, limit))
        except Exception as e:
            errors.append(f"{account['name']}: {type(e).__name__}: {e}")

    if not results and errors:
        return "error: " + "; ".join(errors)
    if not results:
        return "No email accounts are configured (GMAIL_IMAP_USER/YAHOO_IMAP_USER unset)."

    lines = [
        f"[{r['account']}] From: {r['sender']} | Subject: {r['subject']} | {r['snippet']}"
        for r in results
    ]
    if errors:
        lines.append("(" + "; ".join(errors) + ")")
    return "\n".join(lines)


def _fetch_recent(account: dict, user: str, password: str, limit: int) -> list[dict]:
    conn = imaplib.IMAP4_SSL(account["host"])
    try:
        conn.login(user, password)
        conn.select("INBOX", readonly=True)
        status, data = conn.uid("search", None, "ALL")
        if status != "OK" or not data or not data[0]:
            return []
        uids = data[0].split()[-limit:]  # most recent = highest UIDs
        uids.reverse()

        out = []
        for uid in uids:
            status, msg_data = conn.uid("fetch", uid, "(BODY.PEEK[])")
            if status != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            body = _plain_text_body(msg).strip().replace("\n", " ")
            out.append({
                "account": account["name"],
                "sender": _decode(msg.get("From")),
                "subject": _decode(msg.get("Subject")),
                "snippet": body[:_LIST_SNIPPET_CHARS],
            })
        return out
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def _send_email(args: dict) -> str:
    account = (args.get("account") or "").strip().lower()
    if account not in _SMTP_ACCOUNTS:
        return "error: account must be 'gmail' or 'yahoo'"

    config = _SMTP_ACCOUNTS[account]
    user = getattr(settings, config["user_setting"])
    password = getattr(settings, config["password_setting"])
    if not user or not password:
        return f"error: {account} isn't configured (missing IMAP/app-password credentials)"

    to = args["to"]
    subject = args["subject"]
    body = args["body"]

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to

    try:
        with smtplib.SMTP(config["host"], config["port"], timeout=15) as smtp:
            smtp.starttls()
            smtp.login(user, password)
            smtp.send_message(msg)
    except Exception as e:
        return f"error: failed to send: {type(e).__name__}: {e}"
    return f'Sent from {account} ({user}) to {to}: "{subject}"'


list_recent_emails_tool = Tool(
    name="list_recent_emails",
    description=(
        "Fetches the user's most recent emails (any read/unread state) from their "
        "configured accounts (Gmail and/or Yahoo) via IMAP -- use this whenever they ask "
        "about their email/inbox, NOT the browser or desktop tools (there's no need to "
        "open a browser tab for this, IMAP already has it). Read-only, no confirmation needed."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "How many recent emails to fetch (default 5)."},
            "account": {"type": "string", "description": "Optional: 'gmail' or 'yahoo' to check just one account."},
        },
    },
    tier=PermissionTier.ALLOW,
    handler=_list_recent_emails,
)

send_email_tool = Tool(
    name="send_email",
    description=(
        "Sends an email from the user's Gmail or Yahoo account via SMTP -- a new message or a "
        "reply (for a reply: set subject to 'Re: <original subject>' if it doesn't already start "
        "with Re:, and to the original sender's address, both available from list_recent_emails). "
        "Read the to/subject/body back to the user before calling this so the confirmation prompt "
        "they see is meaningful -- this is a real, irreversible action, always requires their "
        "explicit yes."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "account": {"type": "string", "description": "'gmail' or 'yahoo' -- which account to send from."},
            "to": {"type": "string"},
            "subject": {"type": "string"},
            "body": {"type": "string"},
        },
        "required": ["account", "to", "subject", "body"],
    },
    tier=PermissionTier.CONFIRM,
    handler=_send_email,
)
