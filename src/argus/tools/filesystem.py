import subprocess
from pathlib import Path

from argus.config import PROJECT_ROOT, settings
from argus.tools.base import PermissionTier, Tool


class PathEscapesAllowedRoots(Exception):
    pass


class PathIsDenied(Exception):
    pass


# The project directory is one of real_fs_roots, but .env lives right in
# it with live API keys -- read_file is ALLOW-tier (no confirmation), so
# without this a plain "read that file" could surface secrets in a reply.
# Explicit denylist rather than trying to keep it out of real_fs_roots
# entirely, since everything else in the project directory is meant to be
# reachable.
_DENIED_PATHS = [(PROJECT_ROOT / ".env").resolve()]


def _allowed_roots() -> list[Path]:
    return [settings.workspace_dir.resolve()] + [p.resolve() for p in settings.real_fs_roots]


def _resolve_path(path_str: str) -> Path:
    """Relative paths resolve against the sandboxed workspace (unchanged
    default behavior). Absolute paths are allowed too, but only inside one
    of the configured real_fs_roots (Documents/Downloads/Desktop, and this
    project's own directory) -- not anywhere else on disk. A short denylist
    (currently just .env) is checked first regardless of root."""
    p = Path(path_str)
    candidate = p.resolve() if p.is_absolute() else (settings.workspace_dir / p).resolve()

    if candidate in _DENIED_PATHS:
        raise PathIsDenied(f"'{path_str}' is off-limits (holds live credentials)")

    for root in _allowed_roots():
        if candidate == root or root in candidate.parents:
            return candidate
    raise PathEscapesAllowedRoots(
        f"'{path_str}' resolves outside the allowed roots "
        f"(workspace + {[str(r) for r in settings.real_fs_roots]})"
    )


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
    description=(
        "Read a text file's contents. Relative paths resolve inside the sandboxed "
        "workspace; absolute paths are also allowed if inside Documents, Downloads, "
        "or Desktop."
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
        "List files in a directory. Relative paths resolve inside the sandboxed "
        "workspace; absolute paths are also allowed if inside Documents, Downloads, "
        "or Desktop. Omit path to list the workspace root."
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
        "Write (overwrite) a text file. Relative paths resolve inside the sandboxed "
        "workspace; absolute paths are also allowed if inside Documents, Downloads, "
        "or Desktop. Always requires user confirmation."
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
        "Run a shell command in the sandboxed workspace directory. Executes via "
        "cmd.exe on Windows -- use Windows commands (dir, type, del, copy), not "
        "Unix/bash ones (ls, cat, rm, cp). 30s timeout."
    ),
    input_schema={
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    },
    tier=PermissionTier.CONFIRM,
    handler=_run_shell,
)
