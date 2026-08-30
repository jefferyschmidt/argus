import subprocess
from pathlib import Path

from argus.config import settings
from argus.tools.base import PermissionTier, Tool


def _resolve_path(path_str: str) -> Path:
    """Resolves local paths. Relative paths use Argus's workspace; absolute
    paths may point anywhere the operating-system account can access."""
    p = Path(path_str)
    return p.resolve() if p.is_absolute() else (settings.workspace_dir / p).resolve()


def _read_file(args: dict) -> str:
    path = _resolve_path(args["path"])
    if not path.exists():
        return f"error: {args['path']} does not exist"
    return path.read_text(errors="replace")


def _write_file(args: dict) -> str:
    from argus.undo_log import snapshot_before_write

    path = _resolve_path(args["path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_before_write(path)
    path.write_text(args["content"])
    return f"wrote {len(args['content'])} chars to {args['path']}"


def _list_dir(args: dict) -> str:
    path = _resolve_path(args.get("path", "."))
    if not path.exists():
        return f"error: {args.get('path', '.')} does not exist"
    entries = sorted(p.name + ("/" if p.is_dir() else "") for p in path.iterdir())
    return "\n".join(entries) if entries else "(empty)"


def _run_shell(args: dict) -> str:
    cwd = _resolve_path(args.get("cwd", str(settings.workspace_dir)))
    result = subprocess.run(
        args["command"],
        shell=True,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
    )
    output = result.stdout + result.stderr
    return output[:4000] if output else f"(no output, exit code {result.returncode})"


read_file_tool = Tool(
    name="read_file",
    description=(
        "Read a text file's contents. Relative paths resolve from Argus's default "
        "workspace; absolute paths may be anywhere the local account can access."
    ),
    input_schema={
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
    tier=PermissionTier.ALLOW,
    handler=_read_file,
)

list_dir_tool = Tool(
    name="list_dir",
    description=(
        "List files in a directory. Relative paths resolve from Argus's default "
        "workspace; absolute paths may be anywhere the local account can access. "
        "Omit path to list the workspace root."
    ),
    input_schema={
        "type": "object",
        "properties": {"path": {"type": "string"}},
    },
    tier=PermissionTier.ALLOW,
    handler=_list_dir,
)

write_file_tool = Tool(
    name="write_file",
    description=(
        "Write (overwrite) a text file. Relative paths resolve from Argus's default "
        "workspace; absolute paths may be anywhere the local account can access. "
        "Always requires user confirmation."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
    },
    tier=PermissionTier.CONFIRM,
    handler=_write_file,
)

run_shell_tool = Tool(
    name="run_shell",
    description=(
        "Run a shell command through cmd.exe on Windows. Defaults to Argus's workspace; "
        "pass an absolute cwd to run in any local folder. Use Windows commands (dir, type, "
        "del, copy), not Unix/bash ones (ls, cat, rm, cp). 30s timeout."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "cwd": {"type": "string", "description": "Optional working directory; absolute paths are allowed."},
        },
        "required": ["command"],
    },
    tier=PermissionTier.CONFIRM,
    handler=_run_shell,
)
