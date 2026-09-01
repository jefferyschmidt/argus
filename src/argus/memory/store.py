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

CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    due_at TEXT NOT NULL,
    text TEXT NOT NULL,
    completed INTEGER NOT NULL DEFAULT 0,
    notified INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(due_at);

CREATE TABLE IF NOT EXISTS journal_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL DEFAULT (datetime('now')),
    text TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS routines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    time_of_day TEXT NOT NULL,
    goal TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_run_date TEXT
);

CREATE TABLE IF NOT EXISTS research_topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    topic TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_checked_at TEXT,
    last_digest TEXT
);

CREATE TABLE IF NOT EXISTS kg_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    object TEXT NOT NULL,
    UNIQUE(subject, predicate, object)
);

CREATE INDEX IF NOT EXISTS idx_kg_facts_subject ON kg_facts(subject);
CREATE INDEX IF NOT EXISTS idx_kg_facts_object ON kg_facts(object);

-- Single-row cursor: the highest episode id already considered for memory
-- consolidation, so the same episodes are never re-summarized.
CREATE TABLE IF NOT EXISTS consolidation_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_episode_id INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO consolidation_state (id, last_episode_id) VALUES (1, 0);

-- PRD.md §4.1 -- the world model's persisted "things not yet resolved."
-- opened_by_obs_id references a spine observation's row id, which lives
-- in the separate spine.db (P1) -- no FK constraint across databases, so
-- it's stored as a plain int.
CREATE TABLE IF NOT EXISTS threads (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    kind              TEXT    NOT NULL,   -- email_reply | commitment | system_health | task | manual
    title             TEXT    NOT NULL,
    subject           TEXT,
    opened_ts         REAL    NOT NULL,
    opened_by_obs_id  INTEGER,
    close_condition   TEXT    NOT NULL DEFAULT '{}',
    closed_ts         REAL,
    closed_reason     TEXT,
    last_activity_ts  REAL,
    sensitivity       TEXT    NOT NULL DEFAULT 'normal',
    metadata          TEXT    NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_threads_open ON threads(closed_ts, last_activity_ts);

-- PRD.md Appendix A.4 -- derived behavioral baselines, recomputed once
-- daily from the spine, never on a hot path. One row per named baseline;
-- `name` is one of 'active_hours' | 'app_class' | 'sender_importance' |
-- 'session_length'.
CREATE TABLE IF NOT EXISTS rhythms (
    name          TEXT PRIMARY KEY,
    value         TEXT NOT NULL,
    days_observed INTEGER NOT NULL,
    samples       INTEGER NOT NULL,
    confidence    REAL NOT NULL,
    computed_ts   REAL NOT NULL
);
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
