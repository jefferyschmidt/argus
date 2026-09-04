# Argus — working agreement

Two documents are the **source of truth** for how this system fits together. Read them; keep
them true.

- **`SYSTEM_MAP.md`** — who produces and consumes each signal, and whether every subsystem is
  actually wired in production. Update it **in the same commit** as any change to a producer,
  consumer, store, observation kind, or subsystem. A stale map is worse than none.
- **`INVARIANTS.md`** — the rules that, when broken, have caused real bugs here. Read the
  checklist before signing off any gate.

## The gate ritual (do this at the end of every round of code changes)

1. Run the full suite. `tests/test_system_integrity.py` mechanically enforces the connection,
   producer/consumer, and orphan-subsystem invariants, so a green suite already proves those.
2. Invoke the **`argus-audit` skill** for the judgment parts the tests can't hold (semantic
   "is this really wired," sensor-mutation, shared-registry, reversal-safety). Do this *before*
   declaring a unit done, not after shipping it.
3. Reconcile any finding against `SYSTEM_MAP.md` and fix or record it.

The one rule behind all of this: **built and unit-tested is not "done." Done is a real
production producer and consumer, shown in the map.** A unit test that calls `fire()` directly
proves nothing about whether anything calls `fire()` in `src/`. That gap is this project's most
expensive, most repeated bug — the whole point of these artifacts is to close it.

## Diagnosis discipline

Before speccing a fix, reproduce the mechanism empirically (a socket test, a bare call, a
measured timing). A plausible-but-unverified diagnosis has cost a wasted unit here more than
once (INVARIANTS.md I7).
