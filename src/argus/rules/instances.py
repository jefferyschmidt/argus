"""PRD.md §7.4 + Appendix A.3 "Effect reversal". Generalizes
undo_log.py's snapshot-before-write pattern (file-specific there) to any
reversible tool_call rule action. Own connection + lock + WAL (P1,
generalized at the U-C4 gate, PRD §5.2) -- the fifth store to need this,
mirroring TaskStore/RuleStore/ThreadStore/RhythmStore.

Lifecycle: armed -> active -> resolved (schema-reserved 'armed' state is
not produced by this build's own code paths -- firing is atomic here,
capture-then-fire-then-active, since every rule kind built so far fires
immediately on match rather than deferring to a later trigger; 'armed'
is left in the vocabulary for that future rule shape rather than
removed). 'expires_ts' is a hard safety timeout, always set, independent
of whatever the owning rule's until_condition says -- so a crash, or a
watched thread that never closes, can't leave the bulb blue forever."""

import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from argus.config import settings

log = logging.getLogger(__name__)

_READ_FAILED = object()  # sentinel: distinguishes "read_tool failed" from a legitimate None/empty prior_state
_MISSING = object()


def _lookup_prior(prior_state: dict, path: str):
    """Resolves a "$prior.attributes.rgb_color" placeholder against what
    read_tool returned. Returns _MISSING if any segment is absent, which
    the caller treats as "this restore cannot be built"."""
    value = prior_state
    for segment in path.removeprefix("$prior.").split("."):
        if not isinstance(value, dict) or segment not in value:
            return _MISSING
        value = value[segment]
    return value


def build_restore_arguments(action: dict, prior_state) -> dict | None:
    """Reconstructs the arguments that undo a fired `tool_call`, or None
    if no correct restore call can be built (see fire(), which refuses to
    fire in that case).

    Confirmed by direct reproduction at the Phase G gate: the original
    `{**arguments, **prior_state}` merge silently failed on the exact
    example this mechanism exists for. A real home_assistant_get_state
    returns {"state": ..., "attributes": {"rgb_color": [255,255,255]}},
    which shares no key with {"entity_id": ..., "rgb_color": [0,0,255]}
    -- so the merge left the blue rgb_color untouched, added two junk
    kwargs, raised nothing, and marked the instance resolved. The bulb
    stayed blue forever while Argus believed it had cleaned up.

    The correct mechanism is an explicit template on the action:

        "restore_arguments": {"entity_id": "light.office",
                              "rgb_color": "$prior.attributes.rgb_color"}

    The flat merge is kept only for the case where it is actually
    meaningful -- prior_state sharing at least one key with the write
    arguments, i.e. read and write shapes genuinely line up. Anything
    else returns None rather than guessing."""
    original_arguments = action.get("arguments") or {}
    template = action.get("restore_arguments")

    if isinstance(template, dict):
        if not isinstance(prior_state, dict):
            return None
        resolved = {}
        for key, value in template.items():
            if isinstance(value, str) and value.startswith("$prior."):
                looked_up = _lookup_prior(prior_state, value)
                if looked_up is _MISSING:
                    log.warning("restore_arguments placeholder %r not found in read_tool output", value)
                    return None
                resolved[key] = looked_up
            else:
                resolved[key] = value
        return resolved

    if isinstance(prior_state, dict) and set(prior_state) & set(original_arguments):
        return {**original_arguments, **prior_state}
    return None

SCHEMA = """
CREATE TABLE IF NOT EXISTS rule_instances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id INTEGER NOT NULL, state TEXT NOT NULL,
    fired_ts REAL, resolved_ts REAL,
    watched_thread_id INTEGER,
    prior_state TEXT,
    expires_ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rule_instances_state ON rule_instances(state);
"""


@dataclass
class RuleInstance:
    id: int
    rule_id: int
    state: str
    fired_ts: float | None
    resolved_ts: float | None
    watched_thread_id: int | None
    prior_state: dict | None
    expires_ts: float


def _row_to_instance(row: sqlite3.Row) -> RuleInstance:
    return RuleInstance(
        id=row["id"], rule_id=row["rule_id"], state=row["state"],
        fired_ts=row["fired_ts"], resolved_ts=row["resolved_ts"],
        watched_thread_id=row["watched_thread_id"],
        prior_state=json.loads(row["prior_state"]) if row["prior_state"] else None,
        expires_ts=row["expires_ts"],
    )


class RuleInstanceStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self._path = db_path or (settings.data_dir / "argus.db")
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def create(
        self, *, rule_id: int, state: str, expires_ts: float,
        prior_state: dict | None = None, watched_thread_id: int | None = None, fired_ts: float | None = None,
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO rule_instances (rule_id, state, fired_ts, watched_thread_id, prior_state, expires_ts) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (rule_id, state, fired_ts, watched_thread_id, json.dumps(prior_state) if prior_state is not None else None, expires_ts),
            )
            self._conn.commit()
            return cur.lastrowid

    def get(self, instance_id: int) -> "RuleInstance | None":
        with self._lock:
            row = self._conn.execute("SELECT * FROM rule_instances WHERE id = ?", (instance_id,)).fetchone()
        return _row_to_instance(row) if row else None

    def list_active(self, limit: int = 10_000) -> list[RuleInstance]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM rule_instances WHERE state = 'active' ORDER BY fired_ts LIMIT ?", (limit,)
            ).fetchall()
        return [_row_to_instance(r) for r in rows]

    def _mark_resolved(self, instance_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE rule_instances SET state = 'resolved', resolved_ts = ? WHERE id = ? AND state = 'active'",
                (time.time(), instance_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    # -- firing / resolution (Appendix A.3 "Effect reversal") -----------

    @staticmethod
    def _restore_is_constructible(rule, action: dict, prior_state) -> bool:
        if build_restore_arguments(action, prior_state) is not None:
            return True
        log.warning(
            "Rule %s is reversible but no restore call can be built from what read_tool "
            "returned -- refusing to fire. Give the action an explicit restore_arguments "
            "using $prior.<path> placeholders. (read_tool output keys: %s; write arguments: %s)",
            rule.id,
            sorted(prior_state) if isinstance(prior_state, dict) else type(prior_state).__name__,
            sorted(action.get("arguments") or {}),
        )
        return False

    def fire(
        self, *, rule, registry, watched_thread_id: int | None = None,
        expires_seconds: float = 604_800, now: float | None = None,
    ) -> "int | None":
        """Executes a `tool_call` rule action. Returns the new instance id
        if the action fired and is tracked for reversal, or None if it
        never fired at all (not a tool_call action; not reversible; or --
        the one case that matters most here -- `read_tool` failed).

        If `read_tool` fails, the rule does NOT fire at all (Appendix
        A.3): an irreversible "temporary" change is worse than not
        acting. This is checked and enforced here, not left to the
        caller's discipline."""
        action = rule.action
        now = now if now is not None else time.time()
        if action.get("type") != "tool_call":
            return None

        reversible = bool(action.get("reversible"))
        prior_state = None
        if reversible:
            prior_state = self._capture_prior_state(rule, action, registry)
            if prior_state is _READ_FAILED:
                return None  # already logged inside _capture_prior_state
            # Checked BEFORE firing, for the same reason read_tool is: an
            # effect we cannot reverse must never be applied in the first
            # place. Discovering at resolve() time that the restore can't
            # be built is too late -- the light is already blue.
            if not self._restore_is_constructible(rule, action, prior_state):
                return None

        tool_name = action.get("tool")
        arguments = action.get("arguments", {})
        try:
            result = registry.execute(tool_name, arguments)
        except Exception:
            log.exception("tool_call %s failed to fire for rule %s", tool_name, rule.id)
            return None
        if isinstance(result, str) and result.startswith("error:"):
            log.warning("tool_call %s did not fire for rule %s: %s", tool_name, rule.id, result)
            return None

        if not reversible:
            return None  # fired, but nothing to track for reversal

        return self.create(
            rule_id=rule.id, state="active", prior_state=prior_state,
            watched_thread_id=watched_thread_id, expires_ts=now + expires_seconds, fired_ts=now,
        )

    def _capture_prior_state(self, rule, action: dict, registry):
        read_tool = action.get("read_tool")
        read_arguments = action.get("read_arguments", {})
        if not read_tool:
            log.warning("Rule %s marked reversible with no read_tool -- refusing to fire", rule.id)
            return _READ_FAILED
        try:
            raw = registry.execute(read_tool, read_arguments)
        except Exception:
            log.exception("read_tool %s failed for rule %s -- action will not fire", read_tool, rule.id)
            return _READ_FAILED
        if isinstance(raw, str) and raw.startswith("error:"):
            log.warning("read_tool %s returned an error for rule %s -- action will not fire: %s", read_tool, rule.id, raw)
            return _READ_FAILED
        if isinstance(raw, dict):
            return raw
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {"value": parsed}
        except (TypeError, ValueError):
            return {"value": raw}

    def resolve(self, instance_id: int, *, registry, rule_store) -> bool:
        """Reverses a fired instance's effect: calls the same tool again
        with arguments reconstructed from prior_state (Appendix A.3).
        Left `active` (not force-resolved) if the restore call itself
        fails -- a future reap() retries rather than silently giving up
        on restoring real-world state."""
        instance = self.get(instance_id)
        if instance is None or instance.state != "active":
            return False
        rule = rule_store.get(instance.rule_id)
        if rule is None:
            log.warning("Rule %s for instance %s no longer exists -- marking resolved without restoring", instance.rule_id, instance_id)
            return self._mark_resolved(instance_id)

        action = rule.action
        tool_name = action.get("tool")
        restore_arguments = build_restore_arguments(action, instance.prior_state)
        if restore_arguments is None:
            # Should be unreachable: fire() refuses to fire when a restore
            # can't be built. Kept as a guard because reaching it means a
            # real-world effect is stuck on with no way to reverse it --
            # that must be loud and must not be marked resolved.
            log.error(
                "Cannot reconstruct a restore call for instance %s (rule %s, tool %s) -- "
                "leaving it active rather than marking it resolved with the effect still applied",
                instance_id, instance.rule_id, tool_name,
            )
            return False
        try:
            registry.execute(tool_name, restore_arguments)
        except Exception:
            log.exception("Failed to restore prior state for instance %s (tool %s)", instance_id, tool_name)
            return False
        return self._mark_resolved(instance_id)

    def reap(self, *, registry, rule_store, thread_store=None, spine=None, now: float | None = None) -> int:
        """Resolves (and restores) every active instance that's due --
        past its expires_ts safety timeout, OR whose owning rule's
        until_condition is now satisfied for the thread it's watching.
        This is both what a periodic timer would call and what startup
        reconciliation calls (PRD §7.4: "any active instance past
        expires_ts is resolved and its prior_state restored -- otherwise
        a crash leaves the office bulb blue forever'). Never raises: a
        bad predicate or a missing rule must not stop the rest of the
        sweep."""
        now = now if now is not None else time.time()
        resolved = 0
        for instance in self.list_active():
            try:
                due = self._is_due(instance, rule_store, thread_store, spine, now)
            except Exception:
                log.exception("Failed to evaluate due-ness for rule instance %s -- leaving it active", instance.id)
                continue
            if due and self.resolve(instance.id, registry=registry, rule_store=rule_store):
                resolved += 1
        return resolved

    def reconcile_on_startup(self, *, registry, rule_store, thread_store=None, spine=None) -> int:
        return self.reap(registry=registry, rule_store=rule_store, thread_store=thread_store, spine=spine)

    def _is_due(self, instance: RuleInstance, rule_store, thread_store, spine, now: float) -> bool:
        if now >= instance.expires_ts:
            return True
        if instance.watched_thread_id is None or thread_store is None or spine is None:
            return False
        rule = rule_store.get(instance.rule_id)
        if rule is None or not rule.until_condition:
            return False
        from argus.world.predicates import evaluate

        thread = thread_store.get(instance.watched_thread_id)
        return evaluate(rule.until_condition, thread=thread, spine=spine, now=now)
