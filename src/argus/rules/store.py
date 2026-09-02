"""PRD.md §7.1. Storage for standing rules (G1). Own dedicated connection
-- own Connection object, own threading.Lock, WAL -- the same treatment
as SpineStore/ThreadStore (P1): the matcher (§7.3) is read from whichever
thread is scoring a candidate, which is not covered by any single
existing lock the way VoiceLoop's _interaction_lock covers argus.db's
shared connection.

A rule is `proposed` until a human confirms it (RuleCompiler, §7.2) --
reuses CoreMemoryStore's propose/confirm precedent (memory/core.py)."""

import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from argus.config import settings

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    natural_language TEXT NOT NULL,
    source_utterance TEXT,
    kind TEXT NOT NULL,
    trigger TEXT NOT NULL,
    conditions TEXT NOT NULL DEFAULT '[]',
    action TEXT NOT NULL,
    until_condition TEXT,
    group_name TEXT,
    status TEXT NOT NULL DEFAULT 'proposed',
    authorization TEXT,
    created_ts REAL NOT NULL, confirmed_ts REAL, revoked_ts REAL,
    hit_count INTEGER NOT NULL DEFAULT 0, last_fired_ts REAL,
    origin TEXT NOT NULL DEFAULT 'user'
);
CREATE INDEX IF NOT EXISTS idx_rules_status ON rules(status);
"""


@dataclass
class Rule:
    id: int
    natural_language: str
    source_utterance: str | None
    kind: str
    trigger: dict
    conditions: list
    action: dict
    until_condition: dict | None
    group_name: str | None
    status: str
    authorization: dict | None
    created_ts: float
    confirmed_ts: float | None
    revoked_ts: float | None
    hit_count: int
    last_fired_ts: float | None
    origin: str


def _row_to_rule(row: sqlite3.Row) -> Rule:
    return Rule(
        id=row["id"], natural_language=row["natural_language"], source_utterance=row["source_utterance"],
        kind=row["kind"], trigger=json.loads(row["trigger"]), conditions=json.loads(row["conditions"]),
        action=json.loads(row["action"]),
        until_condition=json.loads(row["until_condition"]) if row["until_condition"] else None,
        group_name=row["group_name"], status=row["status"],
        authorization=json.loads(row["authorization"]) if row["authorization"] else None,
        created_ts=row["created_ts"], confirmed_ts=row["confirmed_ts"], revoked_ts=row["revoked_ts"],
        hit_count=row["hit_count"], last_fired_ts=row["last_fired_ts"], origin=row["origin"],
    )


class RuleStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self._path = db_path or (settings.data_dir / "argus.db")
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def propose(
        self, *, natural_language: str, kind: str, trigger: dict, action: dict,
        conditions: list | None = None, until_condition: dict | None = None,
        group_name: str | None = None, authorization: dict | None = None,
        source_utterance: str | None = None, origin: str = "user",
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO rules (natural_language, source_utterance, kind, trigger, conditions, "
                "action, until_condition, group_name, authorization, created_ts, origin) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    natural_language, source_utterance, kind, json.dumps(trigger), json.dumps(conditions or []),
                    json.dumps(action), json.dumps(until_condition) if until_condition is not None else None,
                    group_name, json.dumps(authorization) if authorization is not None else None,
                    time.time(), origin,
                ),
            )
            self._conn.commit()
            return cur.lastrowid

    def confirm(self, rule_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE rules SET status = 'active', confirmed_ts = ? WHERE id = ? AND status = 'proposed'",
                (time.time(), rule_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def disable(self, rule_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE rules SET status = 'disabled' WHERE id = ? AND status = 'active'", (rule_id,)
            )
            self._conn.commit()
            return cur.rowcount > 0

    def revoke(self, rule_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE rules SET status = 'revoked', revoked_ts = ? WHERE id = ? AND status != 'revoked'",
                (time.time(), rule_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def get(self, rule_id: int) -> "Rule | None":
        with self._lock:
            row = self._conn.execute("SELECT * FROM rules WHERE id = ?", (rule_id,)).fetchone()
        return _row_to_rule(row) if row else None

    def list_active(self, kind: str | None = None) -> list[Rule]:
        clauses, params = ["status = 'active'"], []
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        sql = f"SELECT * FROM rules WHERE {' AND '.join(clauses)} ORDER BY created_ts"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_rule(r) for r in rows]

    def list_pending(self) -> list[Rule]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM rules WHERE status = 'proposed' ORDER BY created_ts").fetchall()
        return [_row_to_rule(r) for r in rows]

    def list_by_origin(self, origin: str, *, since: float | None = None) -> list[Rule]:
        """G4 induction (PRD §7.5) reads this in every status, not just
        active/proposed -- a revoked induced rule still counts against
        "don't re-propose a rejected pattern," and every induced rule
        (any status) counts against the weekly proposal cap."""
        clauses, params = ["origin = ?"], [origin]
        if since is not None:
            clauses.append("created_ts >= ?")
            params.append(since)
        sql = f"SELECT * FROM rules WHERE {' AND '.join(clauses)} ORDER BY created_ts DESC"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_rule(r) for r in rows]

    def record_hit(self, rule_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE rules SET hit_count = hit_count + 1, last_fired_ts = ? WHERE id = ?",
                (time.time(), rule_id),
            )
            self._conn.commit()
