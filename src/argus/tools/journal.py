from argus.memory.journal import JournalStore
from argus.memory.store import get_connection
from argus.tools.base import PermissionTier, Tool


def _search_journal(args: dict) -> str:
    query = args.get("query", "").strip()
    conn = get_connection()
    try:
        store = JournalStore(conn)
        rows = store.search(query, limit=10) if query else store.list_recent(limit=10)
    finally:
        conn.close()
    if not rows:
        return "No matching journal entries." if query else "No journal entries yet."
    return "\n".join(f"[{row['ts']}] {row['text']}" for row in rows)


search_journal_tool = Tool(
    name="search_journal",
    description=(
        "Searches the user's voice journal -- freeform spoken notes they've dictated with "
        "'note to self' / 'journal this', not conversation history. Use this when they ask "
        "what they journaled/noted about something, or to recall a past freeform thought. "
        "Omit query to get the most recent entries instead of searching."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Text to search for, or omit for recent entries."},
        },
    },
    tier=PermissionTier.ALLOW,
    handler=_search_journal,
)
