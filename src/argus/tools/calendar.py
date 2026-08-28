from argus.tools.base import PermissionTier, Tool


def _list_calendar_events(args: dict) -> str:
    from argus.google_calendar import NotAuthorized, is_configured, list_upcoming_events

    if not is_configured():
        return "error: Google Calendar isn't set up -- GOOGLE_CALENDAR_CLIENT_ID/SECRET are missing from .env."
    try:
        events = list_upcoming_events(
            max_results=args.get("max_results") or 10,
            days_ahead=args.get("days_ahead") or 14,
        )
    except NotAuthorized as e:
        return f"error: {e}"
    except Exception as e:
        return f"error: {type(e).__name__}: {e}"

    if not events:
        return "No upcoming events in that window."
    lines = []
    for ev in events:
        loc = f" @ {ev['location']}" if ev["location"] else ""
        lines.append(f"{ev['start']}: {ev['summary']}{loc}")
    return "\n".join(lines)


def _create_calendar_event(args: dict) -> str:
    from argus.google_calendar import NotAuthorized, is_configured, create_event

    if not is_configured():
        return "error: Google Calendar isn't set up -- GOOGLE_CALENDAR_CLIENT_ID/SECRET are missing from .env."
    try:
        result = create_event(
            summary=args["summary"],
            start_iso=args["start"],
            end_iso=args["end"],
            description=args.get("description", ""),
        )
    except NotAuthorized as e:
        return f"error: {e}"
    except Exception as e:
        return f"error: {type(e).__name__}: {e}"
    return f"Created \"{args['summary']}\": {result['link']}"


list_calendar_events_tool = Tool(
    name="list_calendar_events",
    description=(
        "Lists the user's upcoming Google Calendar events (default: next 14 days). Use this for "
        "any question about their schedule/calendar/what's coming up. Read-only, no confirmation needed."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "max_results": {"type": "integer", "description": "Max events to return (default 10)."},
            "days_ahead": {"type": "integer", "description": "How many days ahead to look (default 14)."},
        },
    },
    tier=PermissionTier.ALLOW,
    handler=_list_calendar_events,
)

create_calendar_event_tool = Tool(
    name="create_calendar_event",
    description=(
        "Creates a real event on the user's Google Calendar. start/end must be ISO 8601 datetimes with "
        "timezone offset (e.g. '2026-03-05T14:00:00-05:00') -- compute these from what the user asked and "
        "the current date/time already in your context. Always requires user confirmation."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "start": {"type": "string", "description": "ISO 8601 datetime with timezone offset."},
            "end": {"type": "string", "description": "ISO 8601 datetime with timezone offset."},
            "description": {"type": "string"},
        },
        "required": ["summary", "start", "end"],
    },
    tier=PermissionTier.CONFIRM,
    handler=_create_calendar_event,
)
