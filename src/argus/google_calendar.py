"""Real Google Calendar API access (OAuth2), not browser automation --
the user's explicit choice after seeing both tradeoffs. One-time setup:
`argus calendar auth`, which opens a browser once for consent and stores
a refresh token locally; every tool call after that is silent."""

import json
from pathlib import Path

from argus.config import settings

# calendar.events (not the broader `calendar` scope) -- read/write access
# to events specifically, not calendar list management or other calendars'
# settings. Narrowest scope that covers "check my calendar" and "create an
# event."
_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


class NotAuthorized(Exception):
    pass


def _token_path() -> Path:
    return settings.data_dir / "google_calendar_token.json"


def _client_config() -> dict:
    return {
        "installed": {
            "client_id": settings.google_calendar_client_id,
            "client_secret": settings.google_calendar_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }


def is_configured() -> bool:
    return bool(settings.google_calendar_client_id and settings.google_calendar_client_secret)


def run_oauth_flow() -> None:
    """One-time interactive setup -- run from the CLI (`argus calendar
    auth`), not from a live voice/chat turn: popping a browser window mid-
    conversation to ask for OAuth consent would be a confusing UX, so this
    is deliberately a separate, explicit step the user runs themselves
    once, the same way generating an app password was."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not is_configured():
        raise NotAuthorized(
            "GOOGLE_CALENDAR_CLIENT_ID and GOOGLE_CALENDAR_CLIENT_SECRET must be set in .env first."
        )
    flow = InstalledAppFlow.from_client_config(_client_config(), _SCOPES)
    creds = flow.run_local_server(port=0)
    _token_path().write_text(creds.to_json(), encoding="utf-8")


def get_credentials():
    """Returns valid Credentials, refreshing an expired token silently, or
    None if never authorized (caller should report that cleanly, not
    crash -- see the tools in argus/tools/calendar.py)."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    token_path = _token_path()
    if not token_path.exists():
        return None

    creds = Credentials.from_authorized_user_info(json.loads(token_path.read_text(encoding="utf-8")), _SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds


def _service():
    from googleapiclient.discovery import build

    creds = get_credentials()
    if creds is None:
        raise NotAuthorized("Google Calendar isn't authorized yet -- run `argus calendar auth` first.")
    return build("calendar", "v3", credentials=creds)


def list_upcoming_events(max_results: int = 10, days_ahead: int = 14) -> list[dict]:
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    time_max = now + timedelta(days=days_ahead)
    result = (
        _service()
        .events()
        .list(
            calendarId="primary",
            timeMin=now.isoformat(),
            timeMax=time_max.isoformat(),
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    events = []
    for item in result.get("items", []):
        start = item.get("start", {}).get("dateTime") or item.get("start", {}).get("date")
        events.append({
            "id": item.get("id"),
            "summary": item.get("summary", "(no title)"),
            "start": start,
            "location": item.get("location", ""),
        })
    return events


def create_event(summary: str, start_iso: str, end_iso: str, description: str = "") -> dict:
    body = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": start_iso},
        "end": {"dateTime": end_iso},
    }
    result = _service().events().insert(calendarId="primary", body=body).execute()
    return {"id": result.get("id"), "link": result.get("htmlLink")}
