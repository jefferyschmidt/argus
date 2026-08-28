import re

from argus.memory.routines import RoutineStore
from argus.memory.store import get_connection
from argus.tools.base import PermissionTier, Tool

_TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def _create_scheduled_routine(args: dict) -> str:
    time_of_day = args["time_of_day"]
    goal = args["goal"]
    if not _TIME_PATTERN.match(time_of_day):
        return "error: time_of_day must be 24-hour HH:MM, e.g. '07:00' or '18:30'"

    conn = get_connection()
    try:
        routine_id = RoutineStore(conn).add(time_of_day, goal)
    finally:
        conn.close()
    return f"Routine #{routine_id} set for {time_of_day} every day: {goal}"


def _list_scheduled_routines(args: dict) -> str:
    conn = get_connection()
    try:
        rows = RoutineStore(conn).list_all()
    finally:
        conn.close()
    if not rows:
        return "No scheduled routines."
    lines = []
    for row in rows:
        status = "enabled" if row["enabled"] else "disabled"
        lines.append(f"#{row['id']}: {row['time_of_day']} daily -- {row['goal']} ({status})")
    return "\n".join(lines)


def _cancel_scheduled_routine(args: dict) -> str:
    routine_id = args["routine_id"]
    conn = get_connection()
    try:
        RoutineStore(conn).cancel(routine_id)
    finally:
        conn.close()
    return f"Routine #{routine_id} cancelled."


create_scheduled_routine_tool = Tool(
    name="create_scheduled_routine",
    description=(
        "Sets up a recurring daily routine -- Argus runs the goal (full tool access: web "
        "search, reminders, email, etc.) unprompted at the given time every day and speaks "
        "the result, e.g. a morning briefing (weather + today's reminders + anything "
        "important overnight). Compute time_of_day yourself from what the user asked and the "
        "current time already in your context (e.g. 'every morning at 7' -> '07:00'). Write "
        "goal as a clear, complete instruction, as if the user were asking fresh each time -- "
        "it gets fed straight to yourself when it fires."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "time_of_day": {"type": "string", "description": "24-hour HH:MM, e.g. '07:00'"},
            "goal": {"type": "string", "description": "What to do/report when this fires."},
        },
        "required": ["time_of_day", "goal"],
    },
    tier=PermissionTier.ALLOW,
    handler=_create_scheduled_routine,
)

list_scheduled_routines_tool = Tool(
    name="list_scheduled_routines",
    description="Lists all scheduled routines (enabled and disabled), with their times and goals.",
    input_schema={"type": "object", "properties": {}},
    tier=PermissionTier.ALLOW,
    handler=_list_scheduled_routines,
)

cancel_scheduled_routine_tool = Tool(
    name="cancel_scheduled_routine",
    description="Cancels (disables) a scheduled routine by its ID (from list_scheduled_routines).",
    input_schema={
        "type": "object",
        "properties": {"routine_id": {"type": "integer"}},
        "required": ["routine_id"],
    },
    tier=PermissionTier.ALLOW,
    handler=_cancel_scheduled_routine,
)
