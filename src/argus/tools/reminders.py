from datetime import datetime, timedelta

from argus.memory.reminders import ReminderStore
from argus.memory.store import get_connection
from argus.tools.base import PermissionTier, Tool


def _set_reminder(args: dict) -> str:
    text = args["text"]
    minutes_from_now = args["minutes_from_now"]
    if not isinstance(minutes_from_now, (int, float)) or minutes_from_now < 0:
        return "error: minutes_from_now must be a non-negative number"

    due_at = datetime.now().astimezone() + timedelta(minutes=minutes_from_now)
    conn = get_connection()
    try:
        reminder_id = ReminderStore(conn).add(text, due_at.isoformat())
    finally:
        conn.close()
    return (
        f"Reminder #{reminder_id} set for "
        f"{due_at.strftime('%I:%M %p on %A, %B %d').lstrip('0')}: {text}"
    )


def _list_reminders(args: dict) -> str:
    conn = get_connection()
    try:
        pending = ReminderStore(conn).list_pending()
    finally:
        conn.close()
    if not pending:
        return "No pending reminders."
    lines = []
    for row in pending:
        due = datetime.fromisoformat(row["due_at"])
        lines.append(f"#{row['id']}: {row['text']} (due {due.strftime('%I:%M %p on %A, %B %d').lstrip('0')})")
    return "\n".join(lines)


def _cancel_reminder(args: dict) -> str:
    reminder_id = args["reminder_id"]
    conn = get_connection()
    try:
        ReminderStore(conn).complete(reminder_id)
    finally:
        conn.close()
    return f"Reminder #{reminder_id} cancelled."


set_reminder_tool = Tool(
    name="set_reminder",
    description=(
        "Sets a reminder Argus will proactively speak when it's due, even if the "
        "user isn't actively talking to Argus at that moment (checked roughly every "
        "20-30 seconds while idle). You are NOT given a due date/time directly -- "
        "compute minutes_from_now yourself using the current date/time already "
        "provided in your context and what the user asked for (e.g. 'in 20 minutes' "
        "-> 20; 'tomorrow at 9am' -> however many minutes from right now until then; "
        "'in an hour' -> 60)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "What to remind the user about."},
            "minutes_from_now": {
                "type": "number",
                "description": "Minutes from right now until the reminder should fire.",
            },
        },
        "required": ["text", "minutes_from_now"],
    },
    tier=PermissionTier.ALLOW,
    handler=_set_reminder,
)

list_reminders_tool = Tool(
    name="list_reminders",
    description="Lists all pending (not yet fired or cancelled) reminders, with their due times.",
    input_schema={"type": "object", "properties": {}},
    tier=PermissionTier.ALLOW,
    handler=_list_reminders,
)

cancel_reminder_tool = Tool(
    name="cancel_reminder",
    description="Cancels a pending reminder by its ID (from list_reminders).",
    input_schema={
        "type": "object",
        "properties": {
            "reminder_id": {"type": "integer", "description": "The reminder's ID."},
        },
        "required": ["reminder_id"],
    },
    tier=PermissionTier.ALLOW,
    handler=_cancel_reminder,
)
