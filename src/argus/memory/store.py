import sqlite3
from pathlib import Path

from argus.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL DEFAULT (datetime('now')),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    session_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS core_memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL DEFAULT (datetime('now')),
    content TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL DEFAULT 'agent_proposed',
    confirmed INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_episodes_session ON episodes(session_id);
"""


def get_connection(path: Path | None = None) -> sqlite3.Connection:
    db_path = path or (settings.data_dir / "argus.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn
