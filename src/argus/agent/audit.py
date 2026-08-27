import json
from datetime import datetime, timezone
from pathlib import Path


class AuditLog:
    """Append-only JSONL record of everything an autonomous run did. This is
    the thing that makes unsupervised operation reviewable after the fact --
    every tool call, every decision point, in order, with timestamps."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event_type: str, **fields) -> None:
        entry = {"ts": datetime.now(timezone.utc).isoformat(), "type": event_type, **fields}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
