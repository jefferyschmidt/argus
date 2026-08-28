from argus.tools.base import PermissionTier, Tool
from argus.undo_log import list_recent_writes, undo_write


def _undo_last_write(args: dict) -> str:
    return undo_write(args.get("path"))


def _list_recent_writes(args: dict) -> str:
    entries = list_recent_writes(limit=10)
    if not entries:
        return "No tracked file writes yet."
    lines = []
    for entry in entries:
        status = "had a backup taken" if entry.get("backup") else ("new file" if not entry["existed"] else "no backup")
        lines.append(f"{entry['path']} ({status})")
    return "\n".join(lines)


undo_last_write_tool = Tool(
    name="undo_last_write",
    description=(
        "Reverts the most recent file write (from write_file or write_own_source) -- either "
        "restores the file's content from before that write, or removes it if it didn't exist "
        "before. Omit path to undo the single most recent write of any file; pass path to undo "
        "the most recent write to that specific file. No confirmation needed -- undo is itself a "
        "corrective action."
    ),
    input_schema={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Optional -- omit for the most recent write overall."}},
    },
    tier=PermissionTier.ALLOW,
    handler=_undo_last_write,
)

list_recent_writes_tool = Tool(
    name="list_recent_writes",
    description="Lists the most recent tracked file writes (from write_file/write_own_source), for reference before undoing one.",
    input_schema={"type": "object", "properties": {}},
    tier=PermissionTier.ALLOW,
    handler=_list_recent_writes,
)
