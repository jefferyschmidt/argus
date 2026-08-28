import email
import imaplib

from argus.config import settings
from argus.email_watcher import _ACCOUNTS, _decode, _plain_text_body
from argus.tools.base import PermissionTier, Tool

_LIST_SNIPPET_CHARS = 200


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
