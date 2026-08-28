from argus.memory.research_topics import ResearchTopicStore
from argus.memory.store import get_connection
from argus.tools.base import PermissionTier, Tool


def _track_research_topic(args: dict) -> str:
    topic = args["topic"].strip()
    if not topic:
        return "error: topic can't be empty"
    conn = get_connection()
    try:
        topic_id = ResearchTopicStore(conn).add(topic)
    finally:
        conn.close()
    return f"Tracking #{topic_id}: \"{topic}\" -- I'll check periodically and let you know if anything genuinely new comes up."


def _list_research_topics(args: dict) -> str:
    conn = get_connection()
    try:
        rows = ResearchTopicStore(conn).list_all()
    finally:
        conn.close()
    if not rows:
        return "No tracked research topics."
    lines = []
    for row in rows:
        status = "enabled" if row["enabled"] else "disabled"
        checked = row["last_checked_at"] or "never checked yet"
        lines.append(f"#{row['id']}: \"{row['topic']}\" ({status}, last checked: {checked})")
    return "\n".join(lines)


def _untrack_research_topic(args: dict) -> str:
    topic_id = args["topic_id"]
    conn = get_connection()
    try:
        ResearchTopicStore(conn).cancel(topic_id)
    finally:
        conn.close()
    return f"Stopped tracking topic #{topic_id}."


track_research_topic_tool = Tool(
    name="track_research_topic",
    description=(
        "Starts tracking a topic (a competitor, a technology, a hobby, anything) for "
        "proactive research digests -- Argus periodically searches the web for genuinely new "
        "developments and tells you unprompted, staying quiet if there's nothing new rather "
        "than repeating stale info on a schedule. Use this when the user says something like "
        "'keep an eye on X' or 'let me know if anything happens with Y'."
    ),
    input_schema={
        "type": "object",
        "properties": {"topic": {"type": "string", "description": "What to track, as a clear search-able subject."}},
        "required": ["topic"],
    },
    tier=PermissionTier.ALLOW,
    handler=_track_research_topic,
)

list_research_topics_tool = Tool(
    name="list_research_topics",
    description="Lists all tracked research topics (enabled and disabled), with their last-checked time.",
    input_schema={"type": "object", "properties": {}},
    tier=PermissionTier.ALLOW,
    handler=_list_research_topics,
)

untrack_research_topic_tool = Tool(
    name="untrack_research_topic",
    description="Stops tracking a research topic by its ID (from list_research_topics).",
    input_schema={
        "type": "object",
        "properties": {"topic_id": {"type": "integer"}},
        "required": ["topic_id"],
    },
    tier=PermissionTier.ALLOW,
    handler=_untrack_research_topic,
)
