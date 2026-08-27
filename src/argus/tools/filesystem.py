import subprocess
from pathlib import Path

from argus.config import settings
from argus.tools.base import PermissionTier, Tool


class PathEscapesWorkspace(Exception):
    pass


def _resolve_in_workspace(rel_path: str) -> Path:
    root = settings.workspace_dir.resolve()
    candidate = (root / rel_path).resolve()
    if root not in candidate.parents and candidate != root:
        raise PathEscapesWorkspace(f"'{rel_path}' resolves outside the workspace sandbox")
    return candidate


def _read_file(args: dict) -> str:
    path = _resolve_in_workspace(args["path"])
    if not path.exists():
        return f"error: {args['path']} does not exist"
    return path.read_text(errors="replace")


def _write_file(args: dict) -> str:
    path = _resolve_in_workspace(args["path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(args["content"])
    return f"wrote {len(args['content'])} chars to {args['path']}"


def _list_dir(args: dict) -> str:
    path = _resolve_in_workspace(args.get("path", "."))
    if not path.exists():
        return f"error: {args.get('path', '.')} does not exist"
    entries = sorted(p.name + ("/" if p.is_dir() else "") for p in path.iterdir())
    return "\n".join(entries) if entries else "(empty)"


def _run_shell(args: dict) -> str:
    result = subprocess.run(
        args["command"],
        shell=True,
        cwd=settings.workspace_dir,
        capture_output=True,
        text=True,
        timeout=30,
    )
    output = result.stdout + result.stderr
    return output[:4000] if output else f"(no output, exit code {result.returncode})"


read_file_tool = Tool(
    name="read_file",
    description="Read a text file's contents. Path is relative to the sandboxed workspace directory.",
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
    description="List files in a directory relative to the sandboxed workspace. Omit path to list the workspace root.",
    input_schema={
        "type": "object",
        "properties": {"path": {"type": "string"}},
    },
    tier=PermissionTier.ALLOW,
    handler=_list_dir,
)

write_file_tool = Tool(
    name="write_file",
    description="Write (overwrite) a text file. Path is relative to the sandboxed workspace directory.",
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
    description="Run a shell command in the sandboxed workspace directory. 30s timeout.",
    input_schema={
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    },
    tier=PermissionTier.CONFIRM,
    handler=_run_shell,
)
