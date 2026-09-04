---
name: argus-audit
description: Cross-cutting reliability audit of the Argus codebase. Invoke after every round of code changes / at every gate, BEFORE declaring a unit done. Detects the project's most expensive recurring bug — a subsystem built and unit-tested but never driven in production — plus connection-discipline, sensor-mutation, and shared-registry violations. Reconciles SYSTEM_MAP.md and checks INVARIANTS.md.
---

# Argus reliability audit

You are auditing the Argus codebase for the class of bug unit tests structurally cannot catch:
**built and tested, but never wired into production.** Work from evidence (grep/read the actual
`src/`), never from memory or from the report you are auditing. Report findings most-severe
first; if clean, say so plainly and update `SYSTEM_MAP.md`'s "Last audited" date.

## 1. Orphan sweep (the primary check — I3/I4)

For each subsystem method that represents "the thing actually happening," confirm a **non-test
production caller exists in `src/`**:

```
grep -rn '\.fire(\|\.reap(\|\.prune(\|\.run_once(\|\.schedule(\|\.process_due(' src/argus --include=*.py
```

For anything new in this round, run the same for its core method. A method called only from
`tests/` is an orphan — flag it HIGH. "Has a unit test" is never "is wired."

## 2. Observation kinds — producer AND consumer (I2)

Every kind in `spine/observation.py::KINDS` must be both emitted and consumed in `src/`:

```
grep -rn 'kind="<k>"\|report_failure("<k>"\|report_recovery' src/argus   # producer
grep -rn '"<k>"' src/argus | grep -v observation.py                       # consumer refs
```

A kind consumed but never produced (or vice-versa) is a broken feature — flag it. (Known open:
`mail.deleted` has no producer; `calendar.event_changed` is dead.)

## 3. Connection discipline (I1)

```
grep -rln 'sqlite3.connect' src/argus --include=*.py    # must be ONLY src/argus/db.py
```

Any store opening its own raw connection instead of `db.py::open_db` is a P1 / WAL-flake risk.

## 4. Invariant scan (I5–I11)

Read `INVARIANTS.md` and check the round's changes against each:
- **I5** sensors don't mutate/judge (no state writes in a `Sensor.poll`).
- **I6** any tools consumer uses the Orchestrator's full registry, not a bare `build_default_registry()`.
- **I8** reversible actions refuse to fire without a constructible restore; nothing auto-sends.
- **I9** no LLM on a hot path (sensor/matcher/tick/reap).
- **I10** every background thread / subsystem start is individually try/except-wrapped.

## 5. Reconcile SYSTEM_MAP.md

Compare the map's producer/consumer/wired claims to what you just found. Report every divergence.
If the round added a subsystem, store, or kind and the map wasn't updated, that is itself a
finding (I4 / maintenance rule).

## Output

A ranked list of findings (HIGH = orphan/broken feature, MED = invariant risk, LOW = map drift /
dead vocabulary), each with the exact file:line evidence. Then either "map updated, clean" or the
specific edits SYSTEM_MAP.md needs. Do not fix code unless asked — audit and report.
