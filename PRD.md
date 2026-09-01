# Argus — Perception Layer PRD

**Status:** approved for build, 2026-09-01
**Covers:** Phase A (event spine), Phase B (world model), Phase C (salience), Phase I
(autonomous tasks), Phase G (rules, incl. G4 induction), Phase E-compose (documents).
**Does not cover:** Phases D (daemon), F (integrations), H (dashboard) — see §12.
**Companion:** `ROADMAP.md` Parts II–IV for the *why*. This document is the *what* and *how*.

---

## 0. How to use this document

**You are implementing this. Read this section fully before writing any code.**

1. **Build in the order given in §11.** Each unit lists its dependencies. Do not start a unit
   whose dependencies are not merged and green.
2. **One unit = one commit.** Every commit must have: pyflakes clean, its own new tests
   passing, and the full suite passing. Never commit with a failing suite.
3. **Do not refactor or delete existing working code** unless a unit explicitly says to.
   Phase A/B/C run *alongside* the existing seven proactive workers. The workers keep
   announcing exactly as they do today until §7 U-C4 explicitly retires that path. This is a
   strangler-fig migration, not a rewrite. Ripping out working behavior early is the single
   most likely way to break this build.
4. **When this document and your instinct disagree, follow this document.** If it is genuinely
   ambiguous or appears wrong, stop and ask rather than guessing.
5. **Never invent scope.** If a unit does not ask for it, do not build it. Extra "helpful"
   additions are how this becomes unreviewable.
6. **Acceptance criteria are literal.** A unit is done when every checkbox is objectively
   true, not when it looks finished.

### Non-negotiable rules

- **No LLM call in any hot path.** Sensors never call an LLM. The salience matcher's fast
  path never calls an LLM. Rule evaluation never calls an LLM. LLM use is confined to:
  authoring-time rule compilation, salience tie-breaks on genuinely ambiguous candidates,
  task execution, and document composition.
- **Every background thread catches and logs everything.** No exception may escape a worker
  loop. One bad sensor must never take down the others.
- **Every LLM-driven task has a hard budget** in both tokens and wall-clock. No exceptions.
- **Nothing auto-sends.** No email, no message, no post, no transaction. Documents and drafts
  are produced and staged; the user sends them.
- **Induced rules are proposed, never activated.**

---

## 1. Repo conventions you must match

- **Package layout:** `src/argus/<area>/`, tests in `tests/test_<area>_<thing>.py`.
- **Config:** every tunable goes in `src/argus/config.py` as a `settings` field with a
  default, and is documented in `.env.example`. Never read `os.environ` directly.
- **Comments explain *why*, not *what*.** This codebase's style records the reasoning and any
  real bug that motivated the code. Match it. **Only write "confirmed live" / "confirmed as a
  real bug" for something actually observed** — do not fabricate that provenance.
- **Optional things fail soft.** Anything external is wrapped so that its failure degrades one
  feature, never the process. Pattern to copy: `argus/tools/__init__.py::_wire_mcp_server`.
- **Tests:** `pytest`. Run targeted tests during work; full suite before every commit.
  `pyproject.toml`'s `testpaths = ["tests"]` must not be removed — without it pytest walks
  into an unrelated on-disk directory and poisons the run.
- **Lint:** `python -m pyflakes src/argus/<files>` must be clean.
- **Scratch files** go in the session scratchpad, never `/tmp`, never the repo.

### Commands

```bash
.venv/Scripts/python -m pytest tests/test_spine_store.py -q
```

```bash
.venv/Scripts/python -m pytest -q
```

---

## 2. Pitfalls with a history in this repo

These are real bugs that have already cost time here. Do not re-introduce them.

| # | Pitfall | Rule |
|---|---|---|
| P1 | **SQLite threading.** `memory/store.py::get_connection` uses `check_same_thread=False` and is only safe because `_interaction_lock` serializes every caller. **Sensors have no such lock.** | The spine gets its own connection management with its own `threading.Lock` around every write, plus `PRAGMA journal_mode=WAL`. Do not reuse `get_connection` for the spine. |
| P2 | **Blocking the receive thread deadlocks.** Running tool execution inline on the websocket receive thread deadlocked voice confirmation against its own answer. | Never block a thread that is also the only producer of the event you are waiting for. |
| P3 | **Async context managers get garbage-collected.** Entering `__aenter__()` without holding a reference killed a live MCP connection instantly. | Hold context managers as instance attributes for their whole lifetime. |
| P4 | **Duplicate construction spawns duplicate resources.** Building a second `ToolRegistry` silently spawned a second set of MCP subprocesses. | Construct shared services once and pass them in. Never let a component build its own copy of something the caller already has. |
| P5 | **Windows console is cp1252.** A log line containing an emoji raised `UnicodeEncodeError` and produced a `--- Logging error ---` traceback in the live log on 2026-08-31. | See U-A0. Never assume log arguments are ASCII. |
| P6 | **Racing an async API.** Sending a follow-up request immediately after a cancel raced the server and was rejected. | When an external system processes something asynchronously, wait for its confirmation event; never assume immediate effect. |
| P7 | **In-process state is lost on restart.** `_triaged_uids` / `_suppressed_titles` reset every launch, so Argus re-learned the same things forever. | All perception state is persisted. The spine's `dedupe_key` (§3.2) exists precisely to make this free. |

---

## 3. Phase A — Event spine

**Goal:** one durable, queryable timeline of everything Argus observes. Nothing in this phase
makes a judgment about anything.

**New package:** `src/argus/spine/`
*(named `spine`, deliberately not `events`, to avoid confusion with the existing
`argus.ui.events` UI bus, which is unrelated and stays as-is.)*

### 3.1 `src/argus/spine/observation.py`

```python
@dataclass(frozen=True)
class Observation:
    source: str              # emitting sensor, e.g. "window_focus", "mail", "argus.health"
    kind: str                # dotted event type, e.g. "focus.changed", "mail.received"
    ts: float                # unix epoch seconds, UTC
    subject: str | None = None   # the entity this is about (sender, window title, repo name)
    payload: dict = field(default_factory=dict)   # JSON-serializable, source-specific
    confidence: float = 1.0  # 0.0-1.0; sensors that infer rather than measure set this lower
    dedupe_key: str | None = None
```

`kind` uses a fixed vocabulary; adding a new kind means adding it to `KINDS` in this module so
the set stays discoverable. Start with:

```
focus.changed  focus.idle_started  focus.idle_ended
mail.received  mail.deleted
calendar.event_upcoming  calendar.event_changed
reminder.due  routine.due
git.commit  git.branch_stale
argus.integration_failed  argus.credential_failed  argus.spend_recorded
task.started  task.progress  task.finished  task.failed
document.composed
thread.opened  thread.closed
```

### 3.2 `src/argus/spine/store.py`

```sql
CREATE TABLE IF NOT EXISTS observations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           REAL    NOT NULL,
    source       TEXT    NOT NULL,
    kind         TEXT    NOT NULL,
    subject      TEXT,
    payload      TEXT    NOT NULL DEFAULT '{}',
    confidence   REAL    NOT NULL DEFAULT 1.0,
    dedupe_key   TEXT    UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_obs_ts        ON observations(ts);
CREATE INDEX IF NOT EXISTS idx_obs_kind_ts   ON observations(kind, ts);
CREATE INDEX IF NOT EXISTS idx_obs_subject   ON observations(subject);
```

**`dedupe_key` is the mechanism that replaces every in-process "already handled" set (P7).**
A sensor that re-reports the same fact writes the same key; `INSERT OR IGNORE` makes the
re-report a no-op. `MailSensor` sets it to `f"mail:{account}:{uid}"`, which is exactly what
`_triaged_uids` was doing in memory. Sensors become stateless and idempotent as a result.

```python
class SpineStore:
    def __init__(self, db_path: Path | None = None) -> None: ...
        # default: settings.data_dir / "spine.db" -- a SEPARATE file from argus.db.
        # Own connection, own threading.Lock, PRAGMA journal_mode=WAL. See P1.
    def record(self, obs: Observation) -> int | None: ...
        # returns row id, or None if the dedupe_key already existed
    def query(self, *, kinds: list[str] | None = None, source: str | None = None,
              subject: str | None = None, since: float | None = None,
              until: float | None = None, limit: int = 200) -> list[Observation]: ...
        # newest first
    def latest(self, kind: str, subject: str | None = None) -> Observation | None: ...
    def count(self, *, kind: str | None = None, since: float | None = None) -> int: ...
    def prune(self, older_than_days: int) -> int: ...
```

`record()` must never raise to its caller. On a database error it logs and returns `None` — a
sensor failing to write must not kill the sensor.

### 3.3 `src/argus/spine/sensors/base.py`

```python
class Sensor(ABC):
    name: str
    interval_seconds: float = 60.0

    @abstractmethod
    def poll(self) -> Iterable[Observation]: ...

    def run(self, store: SpineStore, stop: threading.Event) -> None:
        """Poll loop. MUST NOT raise. Catches everything from poll(),
        logs it, and continues to the next interval (see §0 non-negotiables)."""
```

### 3.4 Sensors to implement in Phase A

Each is a thin extraction of an existing worker's **detection** half. **Leave the worker's
judgment and announcement code exactly where it is.**

| Sensor | Emits | Source of logic |
|---|---|---|
| `WindowFocusSensor` | `focus.changed`, `focus.idle_started/ended` | `context_awareness._active_window_title` |
| `MailSensor` | `mail.received` | `email_watcher`'s IMAP fetch half only — **not** `_is_important`, which is judgment and moves to Phase C |
| `CalendarSensor` | `calendar.event_upcoming` | `google_calendar` |
| `ReminderSensor` | `reminder.due` | `memory/reminders.py` |
| `RoutineSensor` | `routine.due` | `routine_worker` |
| `GitActivitySensor` | `git.commit`, `git.branch_stale` | new; scan repo dirs from a new `settings.watched_repos` list |
| `ArgusHealthSensor` | `argus.integration_failed`, `argus.credential_failed`, `argus.spend_recorded` | new; the Yahoo `AUTHENTICATIONFAILED` observed 2026-08-31 is the motivating case |

### 3.5 `src/argus/spine/engine.py`

`SpineEngine(store)` — constructs and starts every enabled sensor on its own daemon thread.
Same construct/`start()` split as `ProactiveEngine`, for the same reason.

### 3.6 CLI

Add `argus timeline [--since 24h] [--kind mail.received] [--limit 50]` to `cli.py`, matching
the existing command style. This is the proof Phase A works and is independently useful.

### 3.7 Phase A acceptance criteria

- [ ] `SpineStore` writes and reads back an `Observation` with every field preserved.
- [ ] Recording the same `dedupe_key` twice inserts one row; second call returns `None`.
- [ ] A `Sensor.poll()` that raises is logged and the loop continues; other sensors unaffected.
- [ ] Two threads writing concurrently produce no `database is locked` error (P1).
- [ ] Observations written before a process restart are readable after it (P7).
- [ ] `argus timeline` prints recent observations, newest first.
- [ ] `store.record()` returns `None` rather than raising when the DB is unwritable.
- [ ] **Every existing proactive worker still announces exactly as before.** Full suite green.

### U-A0 — Prerequisite fix (do this first, it is small)

`logging_config.py` must force UTF-8 on its handlers, or a non-ASCII character in a log
argument raises `UnicodeEncodeError` on the Windows console (P5, observed 2026-08-31 with an
emoji in an email subject). Set `encoding="utf-8"` on the file handler and wrap the stream
handler so it cannot raise. Add a test that logging a string containing an emoji does not
raise.

---

## 4. Phase B — World model

**Goal:** a persisted, continuously-maintained model of the **present**, projected from the
spine. Depends on: Phase A.

**New package:** `src/argus/world/`

### 4.1 Threads — `src/argus/world/threads.py`

```sql
CREATE TABLE IF NOT EXISTS threads (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    kind              TEXT    NOT NULL,   -- email_reply | commitment | system_health | task | manual
    title             TEXT    NOT NULL,
    subject           TEXT,
    opened_ts         REAL    NOT NULL,
    opened_by_obs_id  INTEGER,
    close_condition   TEXT    NOT NULL DEFAULT '{}',   -- JSON predicate
    closed_ts         REAL,
    closed_reason     TEXT,
    last_activity_ts  REAL,
    sensitivity       TEXT    NOT NULL DEFAULT 'normal',  -- 'normal' | 'private'
    metadata          TEXT    NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_threads_open ON threads(closed_ts, last_activity_ts);
```

**`sensitivity` is written but never read in Phases A–I.** It exists so the deferred
speaker-identity/disclosure work (`ROADMAP.md` Part IV) does not require a migration later.
Do not build behavior on it. Do not remove it.

```python
class ThreadStore:
    def open(self, kind, title, *, subject=None, close_condition=None,
             opened_by_obs_id=None, metadata=None) -> int
    def close(self, thread_id: int, reason: str) -> bool
    def open_threads(self, *, kind: str | None = None, limit: int = 50) -> list[Thread]
    def touch(self, thread_id: int) -> None
    def find_open(self, kind: str, subject: str) -> Thread | None
```

Thread openers to implement:
- `mail.received` where the mail is judged important → `email_reply` thread.
- A spoken commitment detected in a user transcript ("I'll look at that tomorrow") →
  `commitment` thread. Detection is one cheap LLM call **at turn end only**, never per event.
- `argus.integration_failed` / `argus.credential_failed` → `system_health` thread, deduped by
  subject so a repeated failure touches one thread instead of opening hundreds.

### 4.2 Rhythms — `src/argus/world/rhythms.py`

Derived baselines over the spine: typical active hours, focus vs. browsing applications,
which senders the user actually acts on, typical uninterrupted session length.

**Honest-uncertainty requirement (`ROADMAP.md` P1):** `RhythmSummary` carries a `confidence`
per baseline and a `days_observed` count. Below `settings.rhythm_min_days` (default 14) a
baseline reports `confidence=0.0` and **must not be asserted as fact anywhere**. Recompute on
a slow cadence; never per event.

### 4.3 Snapshot — `src/argus/world/model.py`

```python
@dataclass
class WorldSnapshot:
    now: datetime
    focus: FocusState | None       # current app/title, minutes on it, confidence
    open_threads: list[Thread]
    horizon: list[CalendarItem]    # next settings.horizon_hours (default 12)
    devices: dict                  # populated in Phase F; {} until then
    health: list[HealthIssue]
    rhythms: RhythmSummary

    def to_prompt_block(self, max_chars: int = 1200) -> str: ...
```

`to_prompt_block` is injected into prompts and is therefore **hard-capped**. Most salient
first, truncate the tail, never exceed `max_chars`. A snapshot that blows out a prompt budget
is a bug.

`WorldModel.snapshot()` must be cheap enough to call on every turn: cached with a short TTL
(`settings.world_snapshot_ttl_seconds`, default 5), invalidated on thread open/close.

### 4.4 Phase B acceptance criteria

- [ ] Opening and closing a thread persists across restart.
- [ ] A repeated `argus.credential_failed` for the same subject touches one thread, never opens a second.
- [ ] `to_prompt_block()` output never exceeds `max_chars` for any input, including 500 open threads.
- [ ] `snapshot()` called 100× in a loop makes at most one underlying recompute within the TTL.
- [ ] A rhythm with fewer than `rhythm_min_days` of data reports `confidence == 0.0`.
- [ ] `sensitivity` defaults to `'normal'` and no code path reads it.
- [ ] Full suite green; existing workers still announce as before.

---

## 5. Phase C — Salience engine

**Goal:** one arbiter replacing seven independent "should I speak?" judgments. Depends on: A, B.

**New package:** `src/argus/salience/`

### 5.1 Types

```python
@dataclass
class Candidate:
    observation_id: int | None
    kind: str
    subject: str | None
    text: str                  # what would be said
    base_urgency: float        # 0.0-1.0 from a static per-kind table
    thread_id: int | None = None

@dataclass
class EscalationStep:
    after_seconds: float
    channel: Literal["speak", "ambient", "push"]

@dataclass
class Decision:
    action: Literal["speak", "hold", "ambient", "push", "suppress"]
    reason: str                        # human-readable; powers "why did you do that?"
    escalation: list[EscalationStep] = field(default_factory=list)
    audience: str | None = None        # reserved; see §4.1 note. Never set in Phases A-I.
```

### 5.2 Scoring — deterministic first

`score(candidate, snapshot) -> float` combines, with no LLM:
static `base_urgency` by kind · thread age and staleness · rhythm fit (is now a time the user
deals with this?) · whether the user is in a meeting or focused · matching rule policy from
Phase G.

An LLM tie-break is permitted **only** when the deterministic score lands in an ambiguous band
(`settings.salience_ambiguous_band`, default 0.45–0.55) **and** at most
`settings.salience_llm_calls_per_hour` (default 6) times per hour. Otherwise the deterministic
score stands.

### 5.3 Interruption budget — `budget.py`

Token bucket: `settings.interruptions_per_hour` (default 3). **Persisted**, so a restart does
not refill it. `speak` consumes budget; `ambient` and `hold` do not. When the budget is empty,
a would-be `speak` becomes `hold`.

### 5.4 Held queue — `held.py`

```sql
CREATE TABLE IF NOT EXISTS held_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_ts REAL NOT NULL, kind TEXT NOT NULL, subject TEXT,
    text TEXT NOT NULL, score REAL NOT NULL,
    thread_id INTEGER, delivered_ts REAL, dismissed_ts REAL
);
```

Held items are delivered at the next natural pause, folded into a briefing, or dismissed.
**Nothing is ever silently dropped** — that binary is the specific defect Phase C exists to
remove. Phase H renders this table; until then `argus held` in the CLI is sufficient.

### 5.5 Escalation — `escalation.py`

A decision may carry timed steps ("repeat in 10 min, push after 30"). One scheduler thread
owns all pending escalations; each step re-checks whether the thread has since closed and
aborts if so. Escalations are persisted and reconciled on startup.

### U-C4 — Retire the workers' own judgment (do this last in Phase C)

Only after every criterion below passes: route each proactive worker's output through
`SalienceEngine` instead of calling `speak_fn` directly, and delete
`ContextAwarenessWorker._generate_prompt`'s standalone "should I say something" LLM call.
Window focus becomes one input among many, not its own reason to talk.

### 5.6 Phase C acceptance criteria

- [ ] With budget exhausted, a `speak` candidate becomes `hold` and lands in `held_items`.
- [ ] Budget does not refill across a restart.
- [ ] Deterministic scoring runs with zero LLM calls for candidates outside the ambiguous band.
- [ ] The LLM tie-break respects its per-hour cap; the 7th call in an hour falls back to deterministic.
- [ ] An escalation step whose thread closed in the interim does not fire.
- [ ] Every `Decision` carries a non-empty `reason`.
- [ ] After U-C4, no worker calls `speak_fn` directly.
- [ ] Measured: unprompted interruptions per hour ≤ `interruptions_per_hour`.

---

## 6. Phase I — Autonomous tasks

**Goal:** Argus can be told to go and do something over minutes or hours, with queryable
progress. Depends on: Phase A. May be built in parallel with B.

**New package:** `src/argus/tasks/`

```sql
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL, prompt TEXT NOT NULL,
    status TEXT NOT NULL,            -- queued|running|paused|done|failed|cancelled
    created_ts REAL NOT NULL, started_ts REAL, finished_ts REAL,
    progress_note TEXT, result_ref TEXT, error TEXT,
    created_by TEXT NOT NULL DEFAULT 'user',
    budget_tokens INTEGER NOT NULL, budget_seconds REAL NOT NULL,
    tokens_used INTEGER NOT NULL DEFAULT 0
);
```

- Bounded worker pool, `settings.max_concurrent_tasks` (default 2).
- **Hard budgets are enforced, not advisory.** On exceeding either, the task is stopped and
  marked `failed` with the reason. This is where runaway cost lives; there is no exception.
- `progress_note` is updated as the task runs so "how's that coming?" is answerable without
  interrupting it.
- Builds on the existing `agent/runner.py`; extend it, do not duplicate it.
- **On startup, any task left in `running` is marked `failed` with reason
  `"interrupted by restart"`. Never auto-resume** — a half-finished agentic task resumed
  blind is worse than a clean failure the user can retry.
- Completion emits `task.finished` / `task.failed` onto the spine. **The task never announces
  itself**; salience decides how and when to surface it.

New tools registered in `build_default_registry`: `start_task`, `task_status`, `cancel_task`.
Tier: `CONFIRM` for `start_task` (it spends money), `ALLOW` for the other two.

### Phase I acceptance criteria

- [ ] A task exceeding `budget_seconds` is stopped and marked `failed` with that reason.
- [ ] A task exceeding `budget_tokens` likewise.
- [ ] `max_concurrent_tasks` is never exceeded.
- [ ] A `running` task found at startup becomes `failed`, never resumed.
- [ ] Cancelling a running task stops it within 5 seconds.
- [ ] Completion writes exactly one `task.finished` observation.
- [ ] `task_status` returns current progress without blocking the task.

---

## 7. Phase G — Standing rules

Depends on: A (G1), C (G2), B + E (G3), B + G1/G2 (G4). See `ROADMAP.md` Part II Phase G for
the full rationale; this section specifies only the build.

### 7.1 Storage — `src/argus/rules/store.py`

```sql
CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    natural_language TEXT NOT NULL,     -- read back to the user verbatim
    source_utterance TEXT,
    kind TEXT NOT NULL,                 -- suppression | preference | automation
    trigger TEXT NOT NULL,              -- JSON
    conditions TEXT NOT NULL DEFAULT '[]',
    action TEXT NOT NULL,               -- JSON
    until_condition TEXT,               -- JSON, automations only
    group_name TEXT,                    -- named modes, G-delta 1
    status TEXT NOT NULL DEFAULT 'proposed',  -- proposed|active|disabled|revoked
    authorization TEXT,                 -- JSON grant, automations only
    created_ts REAL NOT NULL, confirmed_ts REAL, revoked_ts REAL,
    hit_count INTEGER NOT NULL DEFAULT 0, last_fired_ts REAL,
    origin TEXT NOT NULL DEFAULT 'user' -- user | induced
);

CREATE TABLE IF NOT EXISTS rule_instances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id INTEGER NOT NULL, state TEXT NOT NULL,  -- armed|active|resolved|expired
    fired_ts REAL, resolved_ts REAL,
    watched_thread_id INTEGER,
    prior_state TEXT,                   -- JSON snapshot for effect reversal
    expires_ts REAL NOT NULL            -- safety timeout, always set
);
```

### 7.2 Compiler — `src/argus/rules/compiler.py`

Frontier-model call, **authoring time only**, producing a structured rule. Then:
1. Ask exactly **one** scope-clarifying question when scope is ambiguous.
2. Read the rule back in natural language.
3. Detect conflicts against active rules and surface them **now**, not at runtime.
4. Capture the authorization grant in the same confirmation.

Reuse `CoreMemoryStore`'s propose/confirm precedent. A rule is `proposed` until confirmed.

### 7.3 Matcher — `src/argus/rules/matcher.py`

Deterministic, indexed by `trigger.kind`. **No LLM.** Fuzzy predicates delegate to the
existing triage call and cache the verdict per observation, so N rules referencing "important
email" cost one judgment, not N.

### 7.4 Instances and reversal — `src/argus/rules/instances.py`

Lifecycle `armed → active → resolved`. `prior_state` is captured **before** the action fires
and restored on resolve. Generalizes `undo_log.py`'s snapshot-before-write pattern, which is
file-specific today. On daemon startup, reconcile: any `active` instance past `expires_ts` is
resolved and its `prior_state` restored — otherwise a crash leaves the office bulb blue
forever.

### 7.5 G4 — Induction — `src/argus/rules/induction.py`

Daily. Over spine + world model, looking for: repeated dismissals of the same kind, repeated
identical manual actions, consistent timing of when a category actually gets handled.

- **Proposes only. Never activates.** An induced rule enters as `status='proposed'`,
  `origin='induced'`, and goes through the identical confirmation path as a user-authored one.
- At most `settings.induced_rule_proposals_per_week` (default 2). Exceeding this makes it the
  nagging it exists to remove.
- A proposal the user rejects is recorded so the same pattern is not re-proposed.

### 7.6 Introspection (G-h)

Tools: `list_rules`, `explain_last_action`, `revoke_rule`, `activate_mode`, `deactivate_mode`.
Rules that have never fired after 30 days are flagged for review; rules firing more than daily
are surfaced for confirmation.

### Phase G acceptance criteria

- [ ] A compiled rule round-trips through the store with every field intact.
- [ ] Matching runs with zero LLM calls.
- [ ] Two rules referencing the same fuzzy predicate produce one triage call, not two.
- [ ] A conflicting rule is detected at authoring time and surfaced before confirmation.
- [ ] An `active` instance past `expires_ts` is resolved and its prior state restored on startup.
- [ ] An induced rule is never `active` without an explicit user confirmation.
- [ ] Induced proposals never exceed the weekly cap.
- [ ] A named mode arms and disarms its whole group atomically.
- [ ] `explain_last_action` returns the `Decision.reason` that actually drove the action.

---

## 8. Phase E-compose — Documents

Depends on: B, I. **New package:** `src/argus/compose/`

- `compose(spec, snapshot, spine_query) -> Path` writes to `data/documents/`.
- Formats: Markdown and HTML. Templates live in `src/argus/compose/templates/`.
- Long compositions run as a Phase I task, not inline.
- Emits `document.composed`; salience announces readiness. **Never sends anything anywhere.**
- Tool: `compose_document` (tier `CONFIRM`).

### Acceptance criteria

- [ ] A composed document exists on disk at the returned path.
- [ ] Exactly one `document.composed` observation is emitted.
- [ ] No code path in this package sends email, posts, or uploads.
- [ ] A composition exceeding its task budget fails cleanly, leaving no partial file.

---

## 9. Configuration to add

All in `config.py` with defaults, all documented in `.env.example`:

```
spine_db_path · spine_retention_days (default 400)
watched_repos (list, default [])
rhythm_min_days (14) · horizon_hours (12) · world_snapshot_ttl_seconds (5)
interruptions_per_hour (3) · salience_ambiguous_band (0.45,0.55)
salience_llm_calls_per_hour (6)
max_concurrent_tasks (2) · default_task_budget_tokens · default_task_budget_seconds
induced_rule_proposals_per_week (2)
enable_spine (true) · enable_salience (false until U-C4) · enable_task_runner (false)
```

New subsystems ship **disabled by default** where they change behavior, and are enabled
deliberately once verified live.

---

## 10. Testing requirements

Every unit needs: a happy-path test, a persistence-across-restart test where it holds state, a
failure-isolation test where it runs on a thread, and a test encoding the specific mechanism
of any bug found during the build (not merely its symptom — the existing suite's convention).

Do not test against live external services. Fake the IMAP/calendar/git layer. The spine tests
must use a temporary database file, never the real `data/` one.

---

## 11. Build order

| # | Unit | Depends on |
|---|---|---|
| 1 | U-A0 logging UTF-8 fix | — |
| 2 | `Observation` + `SpineStore` + tests | 1 |
| 3 | `Sensor` base + `SpineEngine` | 2 |
| 4 | `WindowFocusSensor`, `MailSensor`, `ArgusHealthSensor` | 3 |
| 5 | Remaining Phase A sensors | 3 |
| 6 | `argus timeline` CLI | 2 |
| 7 | **Phase A acceptance review** | 1–6 |
| 8 | `ThreadStore` + openers | 7 |
| 9 | `rhythms.py` | 7 |
| 10 | `WorldSnapshot` + `to_prompt_block` | 8, 9 |
| 11 | **Phase B acceptance review** | 8–10 |
| 12 | Task registry + runner + budgets | 7 |
| 13 | Task tools + startup reconciliation | 12 |
| 14 | **Phase I acceptance review** | 12, 13 |
| 15 | Rule store + compiler + matcher (G1) | 7 |
| 16 | Salience scoring + budget + held queue | 11, 15 |
| 17 | Escalation scheduler | 16 |
| 18 | U-C4 retire worker judgment | 16, 17 |
| 19 | **Phase C acceptance review** | 16–18 |
| 20 | Rule instances + reversal (G3) | 11, 19 |
| 21 | G4 induction | 20 |
| 22 | Named modes + introspection tools | 20 |
| 23 | E-compose | 11, 14 |

Steps 7, 11, 14 and 19 are gates. Do not proceed past a gate with an unmet criterion.

---

## 12. Explicitly out of scope

**Phases D (daemon), F (integrations), H (dashboard)** are not specified here. They will be
specified once Phase C ships, because their design depends on decisions made during B and C.
Entry criteria for specifying them: Phase C acceptance passed and the salience engine observed
running live for at least one week.

**Speaker identity and disclosure** are deferred by decision (`ROADMAP.md` Part IV). The only
obligation in this build is the forward-compatible `threads.sensitivity` column and the
`Decision.audience` field — written, never read. Do not build behavior on either.

Also out of scope and not to be built opportunistically: honest-counsel behavior, simulation
and what-if, financial data sources, day-job integrations (Sentinel, Netsparker, Monday,
Entra ID), degraded/offline operation, spine backup and export. All are open items in the
audit awaiting a decision.
