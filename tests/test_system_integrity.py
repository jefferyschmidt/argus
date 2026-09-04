"""Mechanical enforcement of the invariants in INVARIANTS.md and the wiring
claims in SYSTEM_MAP.md. These run as part of the ordinary suite, so the
existing "full suite green before every commit" rule enforces them with no
new ritual -- this is the "forced, not forgotten" backbone. The judgment
parts live in the argus-audit skill; these are the parts a test can hold.

If one of these fails, do not weaken the test -- fix the wiring, or if the
change is deliberate, update the KNOWN_* allowlists below in the same commit
(which forces an explicit human decision instead of silent drift)."""

import pathlib
import re

SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "argus"


def _all_src_text() -> str:
    return "\n".join(
        p.read_text(encoding="utf-8", errors="ignore")
        for p in SRC.rglob("*.py")
        if "__pycache__" not in p.parts
    )


# -- I1: one connection helper -------------------------------------------

def test_no_raw_sqlite_connect_outside_db_helper():
    """INVARIANTS.md I1 / §19 u43a-ii: every store opens through
    argus/db.py::open_db. A raw sqlite3.connect anywhere else reintroduces
    the WAL-transition flake and the P1 shared-connection class of bug."""
    offenders = []
    for p in SRC.rglob("*.py"):
        if "__pycache__" in p.parts or p.name == "db.py":
            continue
        if "sqlite3.connect" in p.read_text(encoding="utf-8", errors="ignore"):
            offenders.append(p.relative_to(SRC).as_posix())
    assert not offenders, f"raw sqlite3.connect outside db.py (use open_db): {offenders}"


# -- I2: every observation kind has a producer and a consumer ------------

# Deliberately allow-listed known gaps -- each is a real finding tracked in
# SYSTEM_MAP.md, not silent drift. Removing an entry here without wiring the
# kind will (correctly) fail this test.
KNOWN_NO_PRODUCER = {"mail.deleted", "calendar.event_changed"}
KNOWN_NO_CONSUMER = {"calendar.event_changed"}
# Kinds emitted dynamically (report_failure/report_recovery take the kind as
# an argument), so a literal kind="..." grep misses their producer.
DYNAMIC_PRODUCERS = {"argus.integration_failed", "argus.credential_failed", "argus.credential_recovered"}


def _kinds() -> set[str]:
    text = (SRC / "spine" / "observation.py").read_text(encoding="utf-8")
    block = text[text.index("KINDS"):text.index("})", text.index("KINDS"))]
    # Every real kind is dotted (focus.changed, tool.auto_approved, ...);
    # requiring a dot excludes payload field names ("thread_id", "via")
    # mentioned in comments inside the frozenset block.
    return set(re.findall(r'"([a-z][a-z0-9_]*\.[a-z0-9_.]+)"', block))


def test_every_observation_kind_has_a_producer():
    """INVARIANTS.md I2. A kind produced by nothing is dead or a broken
    feature (e.g. mail.deleted -> email threads never auto-close on delete)."""
    src = _all_src_text()
    missing = set()
    for kind in _kinds():
        if kind in DYNAMIC_PRODUCERS:
            continue
        if f'kind="{kind}"' not in src and f"kind='{kind}'" not in src:
            missing.add(kind)
    unexpected = missing - KNOWN_NO_PRODUCER
    assert not unexpected, (
        f"observation kinds with no producer (wire them, or add to "
        f"KNOWN_NO_PRODUCER + SYSTEM_MAP.md deliberately): {unexpected}"
    )


def test_every_observation_kind_has_a_consumer():
    """INVARIANTS.md I2. A kind consumed by nothing is dead vocabulary."""
    consumer_text = "\n".join(
        p.read_text(encoding="utf-8", errors="ignore")
        for p in SRC.rglob("*.py")
        if "__pycache__" not in p.parts and p.name != "observation.py"
    )
    missing = {k for k in _kinds() if f'"{k}"' not in consumer_text and f"'{k}'" not in consumer_text}
    unexpected = missing - KNOWN_NO_CONSUMER
    assert not unexpected, f"observation kinds consumed by nothing: {unexpected}"


# -- I6: one shared tool registry, never a bare build_default_registry() -

def test_no_bare_registry_construction():
    """INVARIANTS.md I6: build_default_registry() with no args builds a
    registry missing rules/auth/spine. It should never be called bare in
    src/ -- every consumer takes the Orchestrator's full registry.
    (Footgun that caused the same bug at realtime u33, TaskRunner u39,
    argus-agent CLI.)"""
    src = _all_src_text()
    assert "build_default_registry()" not in src, "bare registry construction (I6) -- pass the full registry"


# -- I3/I4: the reliability-pass subsystems still have production callers -

def test_reliability_subsystems_have_production_callers():
    """INVARIANTS.md I3/I4 + §19: these were all orphaned once (built,
    tested, never driven). Guard against regression -- each must have a
    caller in src/ outside its own defining module and outside tests."""
    checks = {
        "fire(": "rules/instances.py",
        "run_once(": "rules/induction.py",
        "process_due(": "salience/escalation.py",
    }
    src = _all_src_text()
    for method, definer in checks.items():
        # a call somewhere that isn't only the definition
        calls = re.findall(re.escape(method), src)
        assert len(calls) >= 2, f"{method} appears to have no production caller (orphan regression, §19)"
