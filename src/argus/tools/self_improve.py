import subprocess
import sys
from pathlib import Path

from argus.config import PROJECT_ROOT
from argus.tools.base import PermissionTier, Tool

# Deliberately separate from the general filesystem sandbox
# (settings.workspace_dir) -- this is the one place Argus's file tools are
# allowed anywhere near its own source, and it's scoped as narrowly as
# possible: its own package and its own tests, nothing else in the repo
# (not pyproject.toml, not .env, not this tools/ file's own sibling
# modules outside src/argus... well, actually those ARE inside src/argus,
# which is the point -- Argus can edit its whole package, just not
# anything outside it).
_SELF_ROOTS = [(PROJECT_ROOT / "src" / "argus").resolve(), (PROJECT_ROOT / "tests").resolve()]


class OwnSourcePathEscapesAllowedRoots(Exception):
    pass


def _resolve_own_path(path_str: str) -> Path:
    p = Path(path_str)
    if p.is_absolute():
        candidate = p.resolve()
    else:
        # Confirmed live as a real bug: "ui" (unambiguously meant as
        # src/argus/ui -- said right after list_own_source's own listing
        # of the source root showed "ui/" as an entry) used to be resolved
        # ONLY against PROJECT_ROOT, landing on a nonexistent top-level
        # ui/ and getting refused as "outside" even though the intent was
        # completely clear. A relative path already starting with
        # "src/argus" or "tests" is treated as already fully-qualified
        # (resolved against the project root, unchanged from before);
        # anything else -- the common case for this tool -- is resolved
        # against Argus's own source root instead.
        parts = p.parts
        already_qualified = parts[:2] == ("src", "argus") or parts[:1] == ("tests",)
        base = PROJECT_ROOT if already_qualified else PROJECT_ROOT / "src" / "argus"
        candidate = (base / path_str).resolve()
    for root in _SELF_ROOTS:
        if candidate == root or root in candidate.parents:
            return candidate
    raise OwnSourcePathEscapesAllowedRoots(
        f"'{path_str}' is outside Argus's own source (src/argus) and tests -- refusing"
    )


def _read_own_source(args: dict) -> str:
    try:
        path = _resolve_own_path(args["path"])
    except OwnSourcePathEscapesAllowedRoots as e:
        return f"error: {e}"
    if not path.exists():
        return f"error: {args['path']} does not exist"
    if path.is_dir():
        # Confirmed live as a real bug: reading a directory raised a raw
        # PermissionError from Windows (open() on a directory), surfaced
        # verbatim as "permission error" -- confusing and wrong, since
        # it's not a real permissions problem at all, just the wrong tool
        # for a directory. A clear redirect instead of a cryptic OS error.
        return f"error: '{args['path']}' is a directory -- use list_own_source to see what's in it"
    return path.read_text(errors="replace")


def _list_own_source(args: dict) -> str:
    try:
        path = _resolve_own_path(args.get("path") or "src/argus")
    except OwnSourcePathEscapesAllowedRoots as e:
        return f"error: {e}"
    if not path.exists():
        return f"error: {args.get('path')} does not exist"
    entries = sorted(p.name + ("/" if p.is_dir() else "") for p in path.iterdir())
    return "\n".join(entries) if entries else "(empty)"


def _write_own_source(args: dict) -> str:
    from argus.undo_log import snapshot_before_write

    try:
        path = _resolve_own_path(args["path"])
    except OwnSourcePathEscapesAllowedRoots as e:
        return f"error: {e}"
    if path.is_dir():
        return f"error: '{args['path']}' is a directory, not a file -- can't write to it"
    path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_before_write(path)
    path.write_text(args["content"])
    try:
        shown_path = path.relative_to(PROJECT_ROOT)
    except ValueError:
        shown_path = path  # only happens with a monkeypatched _SELF_ROOTS in tests
    return f"wrote {len(args['content'])} chars to {shown_path}"


def _run_own_tests(args: dict) -> str:
    # sys.executable, not the literal "python" -- confirmed live this was
    # a real, currently-broken bug: "python" on PATH resolved to a
    # completely different global install with no dependencies installed
    # (pytest included), so this reported FAILED unconditionally,
    # regardless of whether the actual code change was fine. That
    # silently defeated the one safety check the self-improve system
    # prompt relies on ("never claim success without having actually seen
    # tests pass").
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    output = (result.stdout + result.stderr).strip()
    status = "PASSED" if result.returncode == 0 else "FAILED"
    return f"{status} (exit code {result.returncode})\n{output[-4000:]}"


def _commit_own_changes(args: dict) -> str:
    message = args["message"]
    add = subprocess.run(
        ["git", "add", "--", "src/argus", "tests"], cwd=PROJECT_ROOT, capture_output=True, text=True
    )
    if add.returncode != 0:
        return f"error staging changes: {add.stderr.strip()}"
    commit = subprocess.run(
        ["git", "commit", "-m", message], cwd=PROJECT_ROOT, capture_output=True, text=True
    )
    if commit.returncode != 0:
        return f"error committing (maybe nothing to commit?): {(commit.stdout + commit.stderr).strip()}"
    return commit.stdout.strip() or "committed."


read_own_source_tool = Tool(
    name="read_own_source",
    description=(
        "Reads a file from Argus's own source code (src/argus/...) or its own tests "
        "(tests/...). Use this to look at your own implementation when the user asks you "
        "to fix a bug in yourself or add a capability -- same conversation, no separate mode."
    ),
    input_schema={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "e.g. 'src/argus/orchestrator.py'"}},
        "required": ["path"],
    },
    tier=PermissionTier.ALLOW,
    handler=_read_own_source,
)

list_own_source_tool = Tool(
    name="list_own_source",
    description="Lists files in a directory of Argus's own source or tests. Omit path to list src/argus's top level.",
    input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
    tier=PermissionTier.ALLOW,
    handler=_list_own_source,
)

write_own_source_tool = Tool(
    name="write_own_source",
    description=(
        "Writes (overwrites or creates) a file in Argus's own source (src/argus/...) or "
        "tests (tests/...). Always requires the user's explicit confirmation. Read the "
        "file first if it already exists -- this overwrites the whole file, it doesn't "
        "patch a diff. Run run_own_tests after writing and be honest about the result; "
        "only commit_own_changes once tests actually pass."
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
    high_risk=True,  # editing Argus's own live code -- asked twice, not once
    handler=_write_own_source,
)

run_own_tests_tool = Tool(
    name="run_own_tests",
    description=(
        "Runs Argus's own test suite (pytest) and reports pass/fail with output. Read-only "
        "with respect to source -- safe to run anytime, no confirmation needed. Always run "
        "this after write_own_source and report the real result, never claim success without "
        "having actually run it and seen it pass."
    ),
    input_schema={"type": "object", "properties": {}},
    tier=PermissionTier.ALLOW,
    handler=_run_own_tests,
)

commit_own_changes_tool = Tool(
    name="commit_own_changes",
    description=(
        "Commits currently staged/unstaged changes under src/argus and tests as a real git "
        "commit (never touches anything else in the repo). Only call this after "
        "run_own_tests has actually passed. Requires user confirmation -- this is a "
        "permanent, revertible-but-real change to the project history."
    ),
    input_schema={
        "type": "object",
        "properties": {"message": {"type": "string", "description": "Commit message"}},
        "required": ["message"],
    },
    tier=PermissionTier.CONFIRM,
    high_risk=True,  # permanent project-history change -- asked twice, not once
    handler=_commit_own_changes,
)


def _restart_argus(args: dict) -> str:
    from argus.restart import request_restart

    request_restart()
    return "Restarting now -- back in a few seconds."


restart_argus_tool = Tool(
    name="restart_argus",
    description=(
        "Restarts the entire Argus process. Necessary after a self-edit, since Python "
        "doesn't hot-reload code changes in a running process. Ends the current session "
        "abruptly -- always confirm the user actually wants this right now before calling it."
    ),
    input_schema={"type": "object", "properties": {}},
    tier=PermissionTier.CONFIRM,
    high_risk=True,  # ends the current session abruptly -- asked twice, not once
    handler=_restart_argus,
)
