# Argus — Perception Layer PRD

**Status:** approved for build, 2026-09-01
**Appendix A (mandatory):** exact specifications for the predicate language, salience
scoring, rule trigger/action schemas, and the rhythms algorithm. Read it before building
units 8, 9, 15 or 16 — those four were prose in the body and must not be improvised.

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
    close_condition   TEXT    NOT NULL DEFAULT '{}',   -- JSON predicate; grammar in Appendix A.1
    closed_ts         REAL,
    closed_reason     TEXT,
    last_activity_ts  REAL,
    sensitivity       TEXT    NOT NULL DEFAULT 'normal',  -- 'normal' | 'private'
    metadata          TEXT    NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_threads_open ON threads(closed_ts, last_activity_ts);
```

**Connection discipline (P1) — binding requirement.** `ThreadStore` takes an injected
connection. The first production construction site **must** give it a *dedicated* connection
with the spine's treatment (own connection object, own lock, WAL), **not** the shared
`memory.store.get_connection` one. That shared connection is only safe for concurrent use
because `_interaction_lock` serializes every caller, and neither the `reap()` timer nor a
world-model read from the UI or salience thread is covered by that lock. A per-component lock
does not rescue it either: one `sqlite3.Connection` interleaved by two components holding two
different locks is still unsafe. This is P1 in a new place, and it is latent rather than live
only because nothing constructs a `ThreadStore` in production yet.

**Spine emission — required before the `thread_closed` predicate is usable.** `open()` and
`close()` must record `thread.opened` / `thread.closed` observations onto the spine. Both
kinds are already in the §3.1 vocabulary but nothing emits them, which leaves Appendix A.1's
`thread_closed` predicate wired and permanently dormant, and leaves thread lifecycle off the
timeline that Phase H renders. Needs a spine reference on `ThreadStore`; do it at the same
time as the connection-discipline change above, since both touch construction.

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

**Specified exactly in Appendix A.4 — build from there, not from this paragraph.**
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

**Specified exactly in Appendix A.2, including the weight table and a worked example.**
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

**P1 applies to EVERY store reachable from a worker thread, not just the spine.** This has now
been found three times — `ThreadStore` (Phase B gate), `RhythmStore` (U-C4 gate), and avoided
proactively for `RuleStore`. After U-C4, five worker threads submit candidates concurrently and
`SalienceEngine.decide()` reads rhythms on every one, while the orchestrator uses `argus.db`
from its own thread. Any store constructed with `memory.store.get_connection()` and touched off
the interaction-locked path is unsafe. **New rule: a store reachable from salience, a sensor, a
reap timer, or the UI gets its own connection + lock + WAL, mirroring `RuleStore`.**

**`ProactiveEngine` construction is now on the startup path of both voice loops.** It opens four
databases and starts two subsystems. Every start is individually isolated: a failure there must
degrade proactivity, never prevent Argus from starting.

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
- **Where budget enforcement actually bites, and where it does not.** Checks run at two
  points: at the top of each tool-loop iteration (`check_budget`, before that iteration is
  paid for) and after each tool call (`on_tool_call`). Together these bound the overshoot to
  the single iteration already in flight, and they cover the iterations that end without a
  tool call — which the `on_tool_call` hook alone never saw.
  **Known and accepted limit:** neither hook can interrupt a *single hung tool call* — a
  wedged shell command, or an MCP server that never answers (both observed in this repo).
  A run blocked inside one tool exceeds its wall-clock until that tool returns. Truly
  bounding that needs process isolation, not a callback; it is recorded here as a known bound
  rather than half-solved. Any future work on it belongs with Phase D's daemon, where a task
  can own a real subprocess.
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
    trigger TEXT NOT NULL,              -- JSON; schema in Appendix A.3
    conditions TEXT NOT NULL DEFAULT '[]',
    action TEXT NOT NULL,               -- JSON; schema in Appendix A.3
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
| 10a | ThreadStore dedicated connection + `thread.opened`/`thread.closed` emission (§4.1) | 10 |
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

---

# Appendix A — Specifications that must not be improvised

Added 2026-09-01. Sections 4, 5 and 7 referenced four structures as "JSON" or described them
in prose. Each is load-bearing, and an implementer inventing its own version produces
something that looks right and is quietly wrong. **These four are closed vocabularies. Do not
extend them without an explicit decision recorded in this file.**

An unknown `type`, `op` or `kind` anywhere below must **return a safe default and log a
warning** — never raise, never guess. Safe default means: predicates return `False`, filters
return no-match, scoring contributions return neutral.

---

## A.1 Predicate language (thread `close_condition`, rule `until_condition`)

Used by §4.1 `threads.close_condition` and §7.1 `rule_instances` / `rules.until_condition`.
One evaluator serves both.

### File: `src/argus/world/predicates.py`

```python
def evaluate(predicate: dict, *, thread: Thread | None,
             spine: SpineStore, now: float) -> bool:
    """Pure and deterministic. No LLM. No side effects. Total: an unknown
    'type' logs a warning and returns False, so a malformed predicate
    leaves a thread open rather than silently closing it."""
```

**Failing open is deliberate.** A thread that should have closed and didn't is visible and
annoying; a thread that closes when it shouldn't loses the item silently — the exact defect
Phase C exists to remove.

### The complete vocabulary — seven types, no others

```jsonc
// 1. An observation of this kind has been recorded since the thread opened.
//    "subject" may be the literal string "$thread.subject" to bind to the thread.
{"type": "observation_seen",
 "kind": "mail.replied",
 "subject": "$thread.subject",     // optional; omit to match any subject
 "since": "$thread.opened_ts"}     // optional; or a literal epoch float

// 2. The user explicitly acknowledged this thread (voice "got it", or a
//    dashboard click). Backed by a thread.acknowledged observation.
{"type": "user_acknowledged"}

// 3. Wall-clock timeout measured from thread open.
{"type": "timeout", "seconds": 604800}

// 4. Never closes on its own; only an explicit close() call.
{"type": "manual_only"}

// 5. A named thread's state.
{"type": "thread_closed", "thread_id": 42}

// 6. Numeric comparison against the payload of the newest observation of a kind.
{"type": "value_threshold",
 "kind": "argus.spend_recorded", "field": "payload.usd",
 "op": "gt", "value": 50.0}

// 7. Composition.
{"type": "any_of", "predicates": [ ... ]}
{"type": "all_of", "predicates": [ ... ]}
```

`op` for `value_threshold` is one of: `gt`, `gte`, `lt`, `lte`, `eq`, `neq`.

### Required addition to the spine vocabulary (§3.1)

```
thread.acknowledged     payload: {"thread_id": <int>, "via": "voice" | "ui"}
```

Emitted when the user acknowledges. This is the single observation that closes the bulb loop:
`mail.received` opens an `email_reply` thread → the rule instance watches it →
`thread.acknowledged` satisfies `user_acknowledged` → thread closes → instance resolves →
prior bulb colour restored.

### Default close conditions by thread kind

| Thread kind | Default `close_condition` |
|---|---|
| `email_reply` | `{"type":"any_of","predicates":[{"type":"user_acknowledged"},{"type":"observation_seen","kind":"mail.replied","subject":"$thread.subject"},{"type":"observation_seen","kind":"mail.deleted","subject":"$thread.subject"},{"type":"timeout","seconds":1209600}]}` |
| `commitment` | `{"type":"any_of","predicates":[{"type":"user_acknowledged"},{"type":"timeout","seconds":2592000}]}` |
| `system_health` | `{"type":"manual_only"}` — a broken credential closes when it actually works again, which only the sensor can determine; it calls `close()` directly |
| `task` | `{"type":"any_of","predicates":[{"type":"observation_seen","kind":"task.finished"},{"type":"observation_seen","kind":"task.failed"}]}` |

### Evaluation cadence

`ThreadStore.reap()` evaluates every open thread's condition. Called on a timer
(`settings.thread_reap_seconds`, default 60) and immediately after any observation whose kind
appears in an open thread's condition. **Never on every observation.**

### Acceptance

- [ ] Each of the seven types has a passing and a failing test.
- [ ] An unknown `type` returns `False` and logs, never raises.
- [ ] `$thread.subject` and `$thread.opened_ts` bind correctly.
- [ ] `any_of` / `all_of` nest at least three deep.
- [ ] `reap()` over 1000 open threads makes no LLM call and completes under one second.

---

## A.2 Salience scoring

Replaces §5.2's prose. Every weight is a `settings` field so it is tunable without a code
change.

### File: `src/argus/salience/scoring.py`

### Step 1 — suppression short-circuits, before any scoring

```python
if any(rule matches candidate and rule.action.type == "suppress"):
    return Decision(action="suppress", reason=f"suppressed by rule {rule.id}: {rule.natural_language}")
```

**A suppression can never be outvoted by a high score.** If the user said "stop telling me
about this", no urgency calculation overrides that. This ordering is not negotiable.

### Step 2 — the formula

```python
score = clamp01(
      W_URGENCY   * base_urgency          # 0..1, static table below
    + W_STALENESS * staleness             # 0..1
    + W_RHYTHM    * (rhythm_fit - 0.5)    # -0.5..+0.5, centred so neutral contributes nothing
    + W_RULE      * rule_bias             # -1..+1
    - W_COST      * interruption_cost     # 0..1
)
```

Defaults: `W_URGENCY = 0.45`, `W_STALENESS = 0.20`, `W_RHYTHM = 0.20`, `W_RULE = 0.30`,
`W_COST = 0.35`.

**`base_urgency` — static table, ship exactly this:**

| kind | value |
|---|---|
| `argus.credential_failed` | 0.75 |
| `argus.integration_failed` | 0.60 |
| `reminder.due` | 0.80 |
| `calendar.event_upcoming` | 0.70 |
| `mail.received` (triaged important) | 0.55 |
| `mail.received` (not important) | 0.10 |
| `task.finished` | 0.45 |
| `task.failed` | 0.60 |
| `document.composed` | 0.40 |
| `routine.due` | 0.50 |
| `git.branch_stale` | 0.15 |
| `focus.changed` | 0.05 |
| *(unknown kind)* | 0.30 |

**`staleness`** — `0.0` with no thread; otherwise
`min(1.0, thread_age_hours / settings.staleness_saturation_hours)` (default 72).

**`rhythm_fit`** — `0.5` (neutral) whenever the relevant rhythm's confidence is below
`settings.rhythm_min_confidence` (default 0.5). This is where honest uncertainty enters the
arithmetic: an unproven baseline contributes nothing rather than a guess. Above the threshold,
the fraction of this kind historically handled in the current hour bucket, normalized 0..1.

**`rule_bias`** — sum of matching `boost` rule amounts, clamped to `-1..+1`.

**`interruption_cost`** — first match wins. **Order corrected 2026-09-01:** every `1.0` row
must precede the weaker ones, or "first match wins" is meaningless — as originally written,
`listening paused` (1.0) sat below `focused` (0.7) and never won.

**Binding requirement before U-C4 (unit 18).** Two rows below have no signal source yet. Until
they do, both fall through to `otherwise` (0.3): the meeting row loses 0.245 of score
(`W_COST × 0.7`) against a 0.62 threshold, which means Argus interrupting *during meetings*.
`CalendarSensor` must carry an end time on `calendar.event_upcoming` (or emit
`calendar.event_started` / `calendar.event_ended`) so the meeting row becomes reachable.
The call row stays unreachable until Phase F's call sensor and is accepted as such.
Quiet mode and listening-paused are wired as of the Phase C gate.

**`interruption_cost`** — first match wins:

| state | cost |
|---|---|
| calendar says in a meeting | 1.0 |
| on a call (call sensor active) | 1.0 |
| focused ≥ 25 min in a focus-classified app | 0.7 |
| quiet mode on | 0.6 |
| listening paused | 1.0 |
| active in the last 5 min, not focused | 0.2 |
| idle / away | 0.4 |
| otherwise | 0.3 |

### Step 3 — thresholds

```python
if score >= settings.speak_threshold:      # 0.62
    action = "speak"
elif LOW <= score <= HIGH:                 # ambiguous band, 0.45..0.55
    action = llm_tiebreak(...) or "hold"   # subject to the per-hour cap in §5.2
elif score >= settings.ambient_threshold:  # 0.30
    action = "ambient"
else:
    action = "hold"
```

Then, unconditionally: **if `action == "speak"` and the interruption budget is empty, it
becomes `hold`.** Budget is checked after scoring, never folded into the score.

### Worked example — use this as a test fixture

Important email, 4h-old thread, no matching rules, user active but not focused, rhythm
confidence below threshold:

```
base_urgency      = 0.55  -> 0.45 * 0.55  =  0.2475
staleness         = 4/72  =  0.0556 -> 0.20 * 0.0556 = 0.0111
rhythm_fit        = 0.5 (unproven) -> 0.20 * 0.0 = 0.0
rule_bias         = 0.0
interruption_cost = 0.2   -> -0.35 * 0.2  = -0.0700
                                    score =  0.1886  -> "hold"
```

Correct and deliberate: a merely-important email does not interrupt on its own. It is held,
surfaced at the next pause or in a briefing, and only escalates through staleness or an
explicit rule. Compare the credential failure at 18h with no rules and idle state:

```
0.45*0.75 + 0.20*0.25 + 0 + 0 - 0.35*0.4 = 0.3375 + 0.05 - 0.14 = 0.2475 -> "hold"
```

Also correct: it goes in the health widget and the briefing, not into an interruption. It
reaches `speak` only via a user rule boosting it, which is exactly the intended division of
labour between the engine's defaults and the user's stated preferences.

### Acceptance

- [ ] A suppression rule returns `suppress` without scoring being computed at all.
- [ ] The worked example above reproduces to within 0.001.
- [ ] Unknown kind uses 0.30 and logs once, not per event.
- [ ] Rhythm confidence below threshold contributes exactly 0.0.
- [ ] Budget exhaustion converts `speak` to `hold` and never alters the score.
- [ ] Scoring 10,000 candidates makes zero LLM calls.

---

## A.3 Rule `trigger` and `action` schemas

Replaces the bare "JSON" in §7.1. The compiler (§7.2) emits exactly these shapes; the matcher
(§7.3) indexes on `trigger.kind`.

### Trigger

```jsonc
{"kind": "mail.received",
 "filters": [{"field": "payload.sender", "op": "contains", "value": "@gov"}]}

{"kind": "focus.changed",
 "filters": [{"field": "subject", "op": "contains", "value": "Claude"}]}

{"kind": "thread_opened",
 "filters": [{"field": "kind", "op": "eq", "value": "email_reply"}]}

{"kind": "schedule", "cron": "0 17 * * 5"}      // Fridays 17:00 local

{"kind": "mode_activated", "filters": [{"field": "group", "op": "eq", "value": "focus"}]}
```

`filters` is an implicit AND. An empty list matches every observation of that kind.

**`field`** is a dotted path resolved against the `Observation`: `subject`, `source`,
`confidence`, or `payload.<key>` (nested allowed). An unresolvable path is no-match.

**`op`** — closed set: `eq`, `neq`, `contains`, `not_contains`, `matches` (regex, compiled
once and cached), `gt`, `gte`, `lt`, `lte`, `in` (value is a list), `fuzzy`.

**`fuzzy` is the only op permitted an LLM**, and it must go through the shared per-observation
verdict cache so N rules asking "is this important?" about the same observation cost one call:

```jsonc
{"field": "self", "op": "fuzzy", "value": "an important email"}
```

Cache key is `(observation_id, value)`. Cache is in-memory with a 1-hour TTL; a cache miss on
a rule evaluation for an observation older than the TTL evaluates to `False` rather than
issuing a fresh call, so a backlog replay cannot trigger a burst of LLM calls.

### Action

```jsonc
{"type": "suppress"}

{"type": "boost", "amount": 0.3}                     // -1.0..1.0, feeds rule_bias

{"type": "notify", "channel": "speak", "text": "..."}  // speak | ambient | push

{"type": "tool_call",
 "tool": "home_assistant_set_light",
 "arguments": {"entity_id": "light.office", "rgb_color": [0, 0, 255]},
 "reversible": true,
 "read_tool": "home_assistant_get_state",
 "read_arguments": {"entity_id": "light.office"}}

{"type": "activate_group", "group": "focus"}
{"type": "deactivate_group", "group": "focus"}
```

### Effect reversal — the concrete mechanism

`reversible: true` **requires** `read_tool` and `read_arguments`. The instance lifecycle is:

1. Before firing, call `read_tool(read_arguments)`; store the raw result in
   `rule_instances.prior_state` as JSON.
2. Fire `tool_call`.
3. On resolve (or on `expires_ts`, or on startup reconciliation), call `tool_call`'s tool again
   with arguments reconstructed from `prior_state`.

**If `read_tool` fails, the rule does not fire at all** and the failure is surfaced. An
irreversible "temporary" change is worse than not acting — this is what stops a crash leaving
the bulb blue permanently.

The compiler must refuse to emit `reversible: true` without a working `read_tool`, and must
say so to the user at authoring time.

### Acceptance

- [ ] Every `op` has a passing and a failing test.
- [ ] Unknown `op` is no-match and logs.
- [ ] Two rules with the same `fuzzy` predicate on one observation produce exactly one LLM call.
- [ ] A fuzzy evaluation for an observation older than the cache TTL returns `False` without an LLM call.
- [ ] `reversible: true` with a failing `read_tool` does not fire the action.
- [ ] Prior state round-trips: set, fire, resolve, and the entity returns to its recorded value.

---

## A.4 Rhythms algorithm

Replaces §4.2's prose. Histograms and ratios only — **no ML, no clustering, no libraries.**

### Storage

```sql
CREATE TABLE IF NOT EXISTS rhythms (
    name          TEXT PRIMARY KEY,   -- 'active_hours' | 'app_class' | 'sender_importance' | 'session_length'
    value         TEXT NOT NULL,      -- JSON
    days_observed INTEGER NOT NULL,
    samples       INTEGER NOT NULL,
    confidence    REAL NOT NULL,
    computed_ts   REAL NOT NULL
);
```

Recomputed once daily over a trailing `settings.rhythm_window_days` (default 30). Never
computed on a hot path; readers always read the stored row.

### Confidence — one formula for all four

```python
confidence = 0.0 if days_observed < settings.rhythm_min_days      # default 14
             else min(1.0, (days_observed / rhythm_window_days) * min(1.0, samples / min_samples))
```

`min_samples` per baseline: `active_hours` 200, `app_class` 30 per app, `sender_importance` 5
per sender, `session_length` 20. **A baseline below its floor reports `confidence = 0.0` and
must never be asserted as fact** (`ROADMAP.md` P1) — the scoring function in A.2 already
treats that as neutral.

### The four baselines

**`active_hours`** — 24-bucket histogram of local hour → count of observations of any kind.
Stored normalized against the peak bucket. A bucket ≥ 0.20 of peak is "active."
`value = {"buckets": [0.0, ...24 floats...]}`

**`app_class`** — per app key (the window title truncated at the first ` - ` / ` — `, lowercased):
mean contiguous session minutes and observation count. Classify `focus` if mean session ≥
`settings.focus_session_minutes` (default 12), else `browse`.
`value = {"vscode": {"class": "focus", "mean_minutes": 34.2, "n": 88}, ...}`

**`sender_importance`** — per sender: `acted / received`, where *acted* means a
`mail.replied`, `mail.deleted`, or `thread.acknowledged` observation referencing that sender.
`value = {"julia@…": {"ratio": 0.92, "n": 24}, ...}`

**`session_length`** — median contiguous minutes within one app key, across the window.
`value = {"median_minutes": 27.5}`

### `rhythm_fit` — how A.2 consumes this

For a candidate of kind K at local hour H: from the trailing window, the fraction of
observations of kind K that were *acted on* within hour bucket H, divided by the maximum such
fraction across all buckets. Returns `0.5` when the underlying confidence is below
`rhythm_min_confidence`, so an unproven rhythm is neutral rather than a guess.

### Acceptance

- [ ] A dataset spanning fewer than `rhythm_min_days` yields `confidence == 0.0` for all four.
- [ ] `active_hours` buckets sum consistently and are normalized to a peak of 1.0.
- [ ] An app seen 3 times does not receive a class (below `min_samples`).
- [ ] Recompute over 100,000 observations completes in under 5 seconds and makes zero LLM calls.
- [ ] `rhythm_fit` returns exactly 0.5 for any baseline below the confidence threshold.

---

## A.5 Build-order amendment

Insert into §11, before the units that depend on them:

| # | Unit | Depends on | Insert |
|---|---|---|---|
| 8a | `predicates.py` + `thread.acknowledged` kind | 7 | **before** unit 8 |
| 9a | `rhythms.py` per A.4 (replaces the sketch in unit 9) | 7 | replaces unit 9 |
| 15a | Trigger/action schemas + filter ops + fuzzy cache | 7 | with unit 15 |
| 16a | `scoring.py` per A.2 | 11, 15a | **before** unit 16 |

Units 8a and 9a fall inside the Phase B gate (unit 11); 15a and 16a inside the Phase C gate
(unit 19). Both gates now additionally require every acceptance box in this appendix to be
ticked.
