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
    # check_same_thread=False: MemoryManager's connection is created once
    # (main thread) but voice input, text input, and push-to-talk each run
    # on their own thread and all reach the same orchestrator/memory
    # instance. That's safe here specifically because VoiceLoop's
    # _interaction_lock already serializes every call path that touches
    # this connection -- sqlite3's default same-thread restriction is a
    # blanket guard, not something this app actually needs given that.
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn
