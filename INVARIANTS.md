# Argus Invariants

**One checklist, run at every gate, instead of re-deriving review criteria from memory each
time.** These are the rules that, when broken, have produced real bugs in this project. The
mechanically-checkable ones are enforced by `tests/test_system_integrity.py` (part of the suite,
so the "full suite green before commit" rule enforces them automatically). The rest are review
judgment — read this list before signing off any gate.

Each invariant names the incident that motivated it, so it is clear this is history, not
theory.

---

## Enforced by tests (`tests/test_system_integrity.py`)

- **I1 — One connection helper.** No `sqlite3.connect` outside `argus/db.py`. Every store opens
  through `open_db(path, schema)`. *(Incident: the "database is locked" WAL-transition flake,
  §19 u43a-ii. Also P1 — RhythmStore/ThreadStore each rediscovered the shared-connection bug.)*
- **I2 — Every observation kind has a producer and a consumer.** A kind in `KINDS` emitted by
  nothing, or consumed by nothing, fails the test. *(Incident: `mail.deleted` consumed by thread
  close-conditions but produced by nothing → email threads never auto-close on delete.)*
- **I3 — No orphaned subsystem.** Every store/engine constructed has a production caller of its
  core method (`fire`, `reap`, `prune`, `run_once`, `schedule`). *(Incident: the entire §19
  reliability pass — six subsystems built, unit-tested, never driven.)*

## Review judgment (read at every gate)

- **I4 — Built ≠ wired. Nothing is "done" until `SYSTEM_MAP.md` shows a real production producer
  *and* consumer.** A unit test calling `fire()` directly proves nothing about whether anything
  calls `fire()` in `src/`. Run the cross-cutting grep: `grep -rn '\.<method>(' src/` and confirm
  a non-test caller exists. *(This is the single most expensive recurring bug.)*
- **I5 — Sensors observe, they do not mutate or judge.** A sensor emits an Observation and
  changes no state. *(Incident: ReminderSensor called `mark_notified` → reminders consumed
  before delivery, silently lost. §20 u38.)*
- **I6 — One shared tool registry.** Any component needing tools (voice loops, TaskRunner,
  proactive workers) gets the Orchestrator's full registry — with rules and the authorization
  checker — never a bare `build_default_registry()`. *(Incident: realtime mode §16 u33, then
  TaskRunner §19 u39 — the same bug at two sites, a diminished registry with no rules/auth.)*
- **I7 — Verify a diagnosis empirically before speccing a fix.** Reproduce the mechanism (a
  socket test, a bare `connect()`, a measured timing) before writing the fix. *(Incident: the
  busy_timeout misdiagnosis — a plausible mechanism specced without measuring; the real cause was
  the WAL transition. Cost a wasted unit.)*
- **I8 — Nothing auto-sends; nothing acts irreversibly without a reversal path.** Money never
  moves; email/messages are staged, not sent. A reversible rule action refuses to fire unless a
  correct restore can be built. *(Incident: the bulb reversal merge — restore re-sent the blue
  colour, bulb stayed blue forever while Argus believed it cleaned up. Phase G gate.)*
- **I9 — No LLM call on a hot path.** Sensors, the salience matcher's fast path, rule matching,
  and the reap/tick loops are deterministic. LLM use is confined to authoring-time rule
  compilation, a capped salience tie-break, task execution, and composition.
- **I10 — Every background thread catches and logs everything.** One failing sensor, worker, or
  tick step never takes down the others. Every subsystem start is individually wrapped.
  *(Incident: ProactiveEngine.start moved onto the voice-loop startup path — an unhandled failure
  there would stop Argus starting at all.)*
- **I11 — All perception state is persisted; nothing important lives only in memory.** *(Incident:
  `_triaged_uids`/`_suppressed_titles` reset every restart — P7. The spine `dedupe_key` exists to
  make idempotent re-reporting free.)*

## Historical pitfalls (the P-series, still live)

P2 — never block a thread that is the sole producer of the event it awaits (voice-confirm
deadlock). P3 — hold async context managers as instance attrs (MCP GC bug). P4 — construct shared
services once, pass them in (double MCP subprocess). P5 — never assume log args are ASCII
(cp1252 `UnicodeEncodeError`). P6 — wait for an async API's confirmation event; never assume
immediate effect (response.create/cancel race).
