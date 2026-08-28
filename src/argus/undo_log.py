import json
import shutil
import time
from pathlib import Path

from argus.config import settings


def _undo_dir() -> Path:
    d = settings.data_dir / "undo"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _log_path() -> Path:
    return _undo_dir() / "log.jsonl"


def snapshot_before_write(path: Path) -> None:
    """Called by write_file/write_own_source right before they overwrite
    `path` -- README roadmap item 14 (undo/rollback for risky actions,
    scoped to file writes; a generic undo for desktop actions has no real
    mechanism, so that half of the item stays out of scope). If the file
    already existed, its current content is copied into a timestamped
    backup so a bad overwrite can be reverted; if it didn't exist yet,
    there's nothing to snapshot -- undoing that write just means deleting
    the new file, which undo_write handles from the "existed": False flag."""
    entry: dict = {"ts": time.time(), "path": str(path), "existed": path.exists()}
    if path.exists():
        backup_path = _undo_dir() / f"{int(time.time() * 1000)}_{path.name}"
        shutil.copy2(path, backup_path)
        entry["backup"] = str(backup_path)
    with open(_log_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def list_recent_writes(limit: int = 10) -> list[dict]:
    log_path = _log_path()
    if not log_path.exists():
        return []
    lines = log_path.read_text(encoding="utf-8").splitlines()
    entries = [json.loads(line) for line in lines[-limit:] if line.strip()]
    return list(reversed(entries))  # most recent first


def undo_write(path_str: str | None = None) -> str:
    """Reverts the most recent tracked write to path_str (or the single
    most recent tracked write of any file if path_str is omitted)."""
    for entry in list_recent_writes(limit=500):
        if path_str and entry["path"] != path_str and Path(entry["path"]).name != Path(path_str).name:
            continue
        target = Path(entry["path"])
        if entry.get("backup"):
            backup = Path(entry["backup"])
            if not backup.exists():
                return f"error: backup for {target} is missing on disk -- can't undo this one"
            shutil.copy2(backup, target)
            return f"Restored {target} to its content before that write."
        if not entry["existed"]:
            if target.exists():
                target.unlink()
            return f"Removed {target} -- it didn't exist before that write."
        return f"error: no usable backup recorded for {target}"
    return f"No tracked write found for '{path_str}'." if path_str else "No tracked writes to undo."
