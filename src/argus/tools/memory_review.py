"""Voice/conversational path for reviewing agent-proposed core memories --
previously only reviewable by clicking Confirm/Reject in the console UI or
running `argus memory review` in a terminal. With the memory consolidation
worker now proposing facts unattended every ~10 minutes, pending memories
can pile up invisibly if nobody happens to be looking at the console --
this lets "what do you have pending to remember" and "yes, remember the
second one" work hands-free, the same way everything else in a voice
assistant should.

Direct DB access via a fresh connection (same pattern as
ui/server.py's _resolve_core_memory) rather than routing through the live
orchestrator's in-memory MemoryManager -- a row update works regardless of
which process/instance last touched it."""

from argus.memory.core import CoreMemoryStore
from argus.memory.store import get_connection
from argus.tools.base import PermissionTier, Tool
from argus.ui import events as ui_events


def _list_pending_core_memories(args: dict) -> str:
    conn = get_connection()
    try:
        pending = CoreMemoryStore(conn).list_pending()
    finally:
        conn.close()

    if not pending:
        return "No pending memories to review right now."

    lines = [f"[{row['id']}] {row['content']}" for row in pending]
    return "\n".join(lines)


def _resolve(memory_id: int, confirmed: bool) -> str:
    conn = get_connection()
    try:
        store = CoreMemoryStore(conn)
        pending = {row["id"]: row["content"] for row in store.list_pending()}
        if memory_id not in pending:
            return f"error: no pending memory with id {memory_id} (it may have already been resolved)"
        content = pending[memory_id]
        if confirmed:
            store.confirm(memory_id)
        else:
            store.reject(memory_id)
        core_count = len(store.list_confirmed())
    finally:
        conn.close()

    # Same events the console's own Confirm/Reject buttons publish -- keeps
    # a connected console's pending list in sync with a review done by voice.
    ui_events.publish({"type": "core_memory_resolved", "id": memory_id, "confirmed": confirmed})
    ui_events.publish({"type": "memory", "core": core_count})

    verb = "Confirmed" if confirmed else "Rejected"
    return f'{verb}: "{content}"'


def _confirm_core_memory(args: dict) -> str:
    try:
        memory_id = int(args["memory_id"])
    except (KeyError, TypeError, ValueError):
        return f"error: memory_id must be an integer, got {args.get('memory_id')!r}"
    return _resolve(memory_id, confirmed=True)


def _reject_core_memory(args: dict) -> str:
    try:
        memory_id = int(args["memory_id"])
    except (KeyError, TypeError, ValueError):
        return f"error: memory_id must be an integer, got {args.get('memory_id')!r}"
    return _resolve(memory_id, confirmed=False)


list_pending_core_memories_tool = Tool(
    name="list_pending_core_memories",
    description=(
        "Lists agent-proposed core memories awaiting review (from noticing something worth "
        "remembering mid-conversation, or from the background memory-consolidation worker) -- "
        "each shown with its id. Use whenever asked what's pending to remember, or before "
        "confirming/rejecting one by voice."
    ),
    input_schema={"type": "object", "properties": {}},
    tier=PermissionTier.ALLOW,
    handler=_list_pending_core_memories,
)

confirm_core_memory_tool = Tool(
    name="confirm_core_memory",
    description=(
        "Confirms a pending core memory by id (from list_pending_core_memories) -- it becomes "
        "a standing fact injected into every future conversation. The user's own request to "
        "confirm IS the confirmation; no separate approval needed."
    ),
    input_schema={
        "type": "object",
        "properties": {"memory_id": {"type": "integer"}},
        "required": ["memory_id"],
    },
    tier=PermissionTier.ALLOW,
    handler=_confirm_core_memory,
)

reject_core_memory_tool = Tool(
    name="reject_core_memory",
    description="Rejects (deletes) a pending core memory by id (from list_pending_core_memories).",
    input_schema={
        "type": "object",
        "properties": {"memory_id": {"type": "integer"}},
        "required": ["memory_id"],
    },
    tier=PermissionTier.ALLOW,
    handler=_reject_core_memory,
)
