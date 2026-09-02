import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

from argus.rules.instances import RuleInstanceStore
from argus.rules.store import RuleStore
from argus.spine.observation import Observation
from argus.spine.store import SpineStore
from argus.world.threads import ThreadStore


class FakeRegistry:
    """Minimal stand-in for ToolRegistry.execute() -- RuleInstanceStore
    only ever calls .execute(name, args), so this is all these tests need."""

    def __init__(self):
        self.handlers = {}
        self.calls = []

    def add(self, name, handler):
        self.handlers[name] = handler

    def execute(self, name, args):
        self.calls.append((name, dict(args)))
        handler = self.handlers.get(name)
        if handler is None:
            return f"error: unknown tool '{name}'"
        return handler(args)


class FakeLight:
    """The bulb example's entity -- starts white, tracks every set."""

    def __init__(self):
        self.state = {"rgb_color": [255, 255, 255]}

    def get_state(self, args):
        return json.dumps(self.state)

    def set_state(self, args):
        self.state = {"rgb_color": args["rgb_color"]}
        return "ok"


def _bulb_registry():
    registry = FakeRegistry()
    light = FakeLight()
    registry.add("home_assistant_get_state", light.get_state)
    registry.add("home_assistant_set_light", light.set_state)
    return registry, light


_BULB_ACTION = {
    "type": "tool_call", "tool": "home_assistant_set_light",
    "arguments": {"entity_id": "light.office", "rgb_color": [0, 0, 255]},
    "reversible": True,
    "read_tool": "home_assistant_get_state", "read_arguments": {"entity_id": "light.office"},
}


def _store(tmp_path) -> RuleInstanceStore:
    return RuleInstanceStore(tmp_path / "argus.db")


def _rule_store(tmp_path) -> RuleStore:
    return RuleStore(tmp_path / "argus.db")


def _confirmed_bulb_rule(rule_store, action=None) -> int:
    rule_id = rule_store.propose(
        natural_language="Turn the office light blue when a build fails", kind="automation",
        trigger={"kind": "task.failed", "filters": []}, action=action or _BULB_ACTION,
    )
    rule_store.confirm(rule_id)
    return rule_id


# -- fire(): the happy path ------------------------------------------------

def test_fire_captures_prior_state_and_creates_an_active_instance(tmp_path):
    instances = _store(tmp_path)
    rule_store = _rule_store(tmp_path)
    rule_id = _confirmed_bulb_rule(rule_store)
    rule = rule_store.get(rule_id)
    registry, light = _bulb_registry()

    instance_id = instances.fire(rule=rule, registry=registry, now=1_000_000.0)

    assert instance_id is not None
    instance = instances.get(instance_id)
    assert instance.state == "active"
    assert instance.rule_id == rule_id
    assert instance.prior_state == {"rgb_color": [255, 255, 255]}
    assert light.state == {"rgb_color": [0, 0, 255]}  # the action actually fired


def test_fire_non_reversible_action_fires_but_tracks_nothing(tmp_path):
    instances = _store(tmp_path)
    rule_store = _rule_store(tmp_path)
    action = {**_BULB_ACTION, "reversible": False}
    del action["read_tool"]
    del action["read_arguments"]
    rule_id = _confirmed_bulb_rule(rule_store, action=action)
    rule = rule_store.get(rule_id)
    registry, light = _bulb_registry()

    instance_id = instances.fire(rule=rule, registry=registry)

    assert instance_id is None  # nothing to track
    assert light.state == {"rgb_color": [0, 0, 255]}  # but it did fire
    assert instances.list_active() == []


def test_fire_non_tool_call_action_returns_none(tmp_path):
    instances = _store(tmp_path)
    rule_store = _rule_store(tmp_path)
    rule_id = rule_store.propose(
        natural_language="x", kind="suppression",
        trigger={"kind": "mail.received", "filters": []}, action={"type": "suppress"},
    )
    rule_store.confirm(rule_id)
    rule = rule_store.get(rule_id)

    assert instances.fire(rule=rule, registry=FakeRegistry()) is None


# -- failure mode 1: read_tool fails -> the action must NOT fire at all -----

def test_read_tool_exception_prevents_the_action_from_firing(tmp_path):
    instances = _store(tmp_path)
    rule_store = _rule_store(tmp_path)
    rule_id = _confirmed_bulb_rule(rule_store)
    rule = rule_store.get(rule_id)
    registry, light = _bulb_registry()

    def _boom(args):
        raise RuntimeError("home assistant unreachable")
    registry.handlers["home_assistant_get_state"] = _boom

    instance_id = instances.fire(rule=rule, registry=registry)

    assert instance_id is None
    assert light.state == {"rgb_color": [255, 255, 255]}  # untouched -- never fired
    assert ("home_assistant_set_light", {"entity_id": "light.office", "rgb_color": [0, 0, 255]}) not in registry.calls
    assert instances.list_active() == []


def test_read_tool_error_string_prevents_the_action_from_firing(tmp_path):
    """Some tools signal failure via an "error: ..." string return rather
    than raising -- this repo's own convention across several tools."""
    instances = _store(tmp_path)
    rule_store = _rule_store(tmp_path)
    rule_id = _confirmed_bulb_rule(rule_store)
    rule = rule_store.get(rule_id)
    registry, light = _bulb_registry()
    registry.handlers["home_assistant_get_state"] = lambda args: "error: entity not found"

    instance_id = instances.fire(rule=rule, registry=registry)

    assert instance_id is None
    assert light.state == {"rgb_color": [255, 255, 255]}


def test_reversible_without_read_tool_never_fires(tmp_path):
    instances = _store(tmp_path)
    rule_store = _rule_store(tmp_path)
    action = {**_BULB_ACTION}
    del action["read_tool"]
    rule_id = _confirmed_bulb_rule(rule_store, action=action)
    rule = rule_store.get(rule_id)
    registry, light = _bulb_registry()

    instance_id = instances.fire(rule=rule, registry=registry)

    assert instance_id is None
    assert registry.calls == []  # not even the write tool was attempted
    assert light.state == {"rgb_color": [255, 255, 255]}


def test_tool_call_failure_after_successful_read_creates_no_instance(tmp_path):
    instances = _store(tmp_path)
    rule_store = _rule_store(tmp_path)
    rule_id = _confirmed_bulb_rule(rule_store)
    rule = rule_store.get(rule_id)
    registry, light = _bulb_registry()

    def _boom(args):
        raise RuntimeError("set failed")
    registry.handlers["home_assistant_set_light"] = _boom

    instance_id = instances.fire(rule=rule, registry=registry)

    assert instance_id is None
    assert instances.list_active() == []


# -- resolve(): the restore half of reversal --------------------------------

def test_resolve_restores_prior_state_and_marks_resolved(tmp_path):
    instances = _store(tmp_path)
    rule_store = _rule_store(tmp_path)
    rule_id = _confirmed_bulb_rule(rule_store)
    rule = rule_store.get(rule_id)
    registry, light = _bulb_registry()
    instance_id = instances.fire(rule=rule, registry=registry)
    assert light.state == {"rgb_color": [0, 0, 255]}

    resolved = instances.resolve(instance_id, registry=registry, rule_store=rule_store)

    assert resolved is True
    assert light.state == {"rgb_color": [255, 255, 255]}  # back to white
    instance = instances.get(instance_id)
    assert instance.state == "resolved"
    assert instance.resolved_ts is not None


def test_resolve_nonexistent_instance_returns_false(tmp_path):
    instances = _store(tmp_path)
    rule_store = _rule_store(tmp_path)
    assert instances.resolve(999, registry=FakeRegistry(), rule_store=rule_store) is False


def test_resolve_already_resolved_returns_false(tmp_path):
    instances = _store(tmp_path)
    rule_store = _rule_store(tmp_path)
    rule_id = _confirmed_bulb_rule(rule_store)
    rule = rule_store.get(rule_id)
    registry, _light = _bulb_registry()
    instance_id = instances.fire(rule=rule, registry=registry)
    instances.resolve(instance_id, registry=registry, rule_store=rule_store)

    assert instances.resolve(instance_id, registry=registry, rule_store=rule_store) is False


def test_resolve_leaves_instance_active_if_restore_call_fails(tmp_path):
    instances = _store(tmp_path)
    rule_store = _rule_store(tmp_path)
    rule_id = _confirmed_bulb_rule(rule_store)
    rule = rule_store.get(rule_id)
    registry, light = _bulb_registry()
    instance_id = instances.fire(rule=rule, registry=registry)

    def _boom(args):
        raise RuntimeError("still unreachable")
    registry.handlers["home_assistant_set_light"] = _boom

    resolved = instances.resolve(instance_id, registry=registry, rule_store=rule_store)

    assert resolved is False
    assert instances.get(instance_id).state == "active"  # retry later, not silently given up on


# -- failure mode 2 + 3: reap() / reconcile_on_startup() --------------------

def test_reap_resolves_instances_past_expires_ts(tmp_path):
    """Failure mode 2 & 3: whether the process crashed and came back
    (startup reconciliation) or the watched thread simply never closed,
    an active instance past its safety timeout is resolved and restored
    the same way."""
    instances = _store(tmp_path)
    rule_store = _rule_store(tmp_path)
    rule_id = _confirmed_bulb_rule(rule_store)
    rule = rule_store.get(rule_id)
    registry, light = _bulb_registry()
    instance_id = instances.fire(rule=rule, registry=registry, expires_seconds=100, now=1_000_000.0)

    not_yet = instances.reap(registry=registry, rule_store=rule_store, now=1_000_050.0)
    assert not_yet == 0
    assert instances.get(instance_id).state == "active"

    resolved = instances.reap(registry=registry, rule_store=rule_store, now=1_000_200.0)

    assert resolved == 1
    assert instances.get(instance_id).state == "resolved"
    assert light.state == {"rgb_color": [255, 255, 255]}


def test_reconcile_on_startup_resolves_instances_left_active_by_a_crash(tmp_path):
    """Simulates a process restart: a fresh RuleInstanceStore against the
    same db file finds the instance still 'active' from before, past its
    expires_ts, and restores it -- never auto-resumed as still-in-effect."""
    db_path = tmp_path / "argus.db"
    rule_store = _rule_store(tmp_path)
    rule_id = _confirmed_bulb_rule(rule_store)
    rule = rule_store.get(rule_id)
    registry, light = _bulb_registry()

    original = RuleInstanceStore(db_path)
    instance_id = original.fire(rule=rule, registry=registry, expires_seconds=1.0, now=1_000_000.0)

    restarted = RuleInstanceStore(db_path)
    resolved = restarted.reconcile_on_startup(registry=registry, rule_store=rule_store)

    assert resolved == 1
    assert restarted.get(instance_id).state == "resolved"
    assert light.state == {"rgb_color": [255, 255, 255]}


def test_reap_bad_until_condition_does_not_raise_and_leaves_instance_active(tmp_path):
    instances = _store(tmp_path)
    rule_store = _rule_store(tmp_path)
    action = {**_BULB_ACTION}
    rule_id = rule_store.propose(
        natural_language="x", kind="automation", trigger={"kind": "task.failed", "filters": []},
        action=action, until_condition={"type": "totally_unknown"},
    )
    rule_store.confirm(rule_id)
    rule = rule_store.get(rule_id)
    registry, _light = _bulb_registry()

    threads = ThreadStore(SpineStore(tmp_path / "spine.db"), tmp_path / "argus.db")
    thread_id = threads.open("manual", "watched")
    instance_id = instances.fire(
        rule=rule, registry=registry, watched_thread_id=thread_id, expires_seconds=100_000, now=1_000_000.0,
    )

    resolved = instances.reap(
        registry=registry, rule_store=rule_store, thread_store=threads,
        spine=threads.spine, now=1_000_001.0,
    )

    assert resolved == 0
    assert instances.get(instance_id).state == "active"


# -- the bulb example, end to end -------------------------------------------

def test_bulb_example_end_to_end_thread_close_resolves_before_expiry(tmp_path):
    """PRD Appendix A.1's own worked description: mail.received opens an
    email_reply thread -> the rule instance watches it ->
    thread.acknowledged satisfies user_acknowledged -> thread closes ->
    instance resolves -> prior bulb colour restored. Uses a manual-close
    thread + a `manual_only`-independent until_condition
    (`user_acknowledged`) driven by a real thread.acknowledged
    observation on the spine, resolved well before the safety timeout."""
    instances = _store(tmp_path)
    rule_store = _rule_store(tmp_path)
    spine = SpineStore(tmp_path / "spine.db")
    threads = ThreadStore(spine, tmp_path / "argus.db")

    # Real wall-clock time throughout -- ThreadStore.open() stamps
    # opened_ts with time.time(), and user_acknowledged's predicate
    # filters spine observations with `since=thread.opened_ts`, so a
    # fabricated epoch for the rest of this test would make every
    # acknowledgement look like it happened before the thread opened.
    now = time.time()
    thread_id = threads.open("email_reply", "Reply to boss@company.com", subject="boss@company.com")
    rule_id = rule_store.propose(
        natural_language="Turn the office light blue until I reply to the boss's email", kind="automation",
        trigger={"kind": "mail.received", "filters": []}, action=_BULB_ACTION,
        until_condition={"type": "user_acknowledged"},
    )
    rule_store.confirm(rule_id)
    rule = rule_store.get(rule_id)
    registry, light = _bulb_registry()

    instance_id = instances.fire(
        rule=rule, registry=registry, watched_thread_id=thread_id, expires_seconds=1_000_000, now=now,
    )
    assert light.state == {"rgb_color": [0, 0, 255]}

    # Not acknowledged yet -- nothing to resolve.
    assert instances.reap(registry=registry, rule_store=rule_store, thread_store=threads, spine=spine, now=now + 10) == 0

    spine.record(Observation(
        source="ui", kind="thread.acknowledged", ts=now + 20,
        payload={"thread_id": thread_id, "via": "voice"}, dedupe_key="ack-1",
    ))

    resolved = instances.reap(registry=registry, rule_store=rule_store, thread_store=threads, spine=spine, now=now + 30)

    assert resolved == 1
    assert instances.get(instance_id).state == "resolved"
    assert light.state == {"rgb_color": [255, 255, 255]}


# -- P1: dedicated connection, persistence, concurrency ---------------------

def test_persists_across_restart(tmp_path):
    db_path = tmp_path / "argus.db"
    rule_store = _rule_store(tmp_path)
    rule_id = _confirmed_bulb_rule(rule_store)
    rule = rule_store.get(rule_id)
    registry, _light = _bulb_registry()

    store = RuleInstanceStore(db_path)
    instance_id = store.fire(rule=rule, registry=registry)

    reopened = RuleInstanceStore(db_path)
    instance = reopened.get(instance_id)
    assert instance.state == "active"
    assert instance.prior_state == {"rgb_color": [255, 255, 255]}


def test_each_store_owns_its_own_connection_object(tmp_path):
    a = _store(tmp_path)
    b = _store(tmp_path)
    assert a._conn is not b._conn


def test_concurrent_fire_and_resolve_do_not_raise_database_is_locked(tmp_path):
    instances = _store(tmp_path)
    rule_store = _rule_store(tmp_path)
    errors = []

    def _work(n: int) -> None:
        try:
            registry, _light = _bulb_registry()
            for i in range(15):
                rule_id = _confirmed_bulb_rule(rule_store)
                rule = rule_store.get(rule_id)
                instance_id = instances.fire(rule=rule, registry=registry)
                if instance_id is not None:
                    instances.resolve(instance_id, registry=registry, rule_store=rule_store)
        except Exception as e:  # pragma: no cover - failure path
            errors.append(e)

    threads_ = [threading.Thread(target=_work, args=(n,)) for n in range(5)]
    for t in threads_:
        t.start()
    for t in threads_:
        t.join()

    assert not errors


def _ha_shaped_rule(*, restore_arguments=None):
    """The bulb rule with a REALISTIC home_assistant_get_state response
    shape -- nested under "attributes", sharing no key with the write
    call's arguments."""
    action = {
        "type": "tool_call",
        "tool": "set_light",
        "arguments": {"entity_id": "light.office", "rgb_color": [0, 0, 255]},
        "reversible": True,
        "read_tool": "get_state",
        "read_arguments": {"entity_id": "light.office"},
    }
    if restore_arguments is not None:
        action["restore_arguments"] = restore_arguments
    return SimpleNamespace(id=1, action=action)


def _ha_registry(calls):
    registry = MagicMock()

    def execute(tool, arguments):
        calls.append((tool, arguments))
        if tool == "get_state":
            return {"state": "on", "attributes": {"rgb_color": [255, 255, 255], "brightness": 200}}
        return "ok"

    registry.execute.side_effect = execute
    return registry


def test_unreconstructable_restore_refuses_to_fire(tmp_path):
    """Found by direct reproduction at the Phase G gate. With a realistic
    nested read_tool response, {**arguments, **prior_state} shares no key
    with the write arguments -- so the restore re-sent the ORIGINAL blue
    rgb_color, added junk kwargs, raised nothing, and marked the instance
    resolved. The bulb stayed blue forever while Argus believed it had
    cleaned up. An effect that cannot be reversed must not be applied at
    all, exactly as with a failing read_tool."""
    store = RuleInstanceStore(tmp_path / "rules.db")
    calls: list = []

    instance_id = store.fire(rule=_ha_shaped_rule(), registry=_ha_registry(calls))

    assert instance_id is None, "fired an effect it had no way to reverse"
    assert [tool for tool, _ in calls] == ["get_state"], "the write tool must never be attempted"


def test_explicit_restore_arguments_round_trip_the_real_prior_value(tmp_path):
    """The correct mechanism: a $prior.<path> template that reaches into
    the read_tool's own response shape."""
    store = RuleInstanceStore(tmp_path / "rules.db")
    calls: list = []
    registry = _ha_registry(calls)
    rule = _ha_shaped_rule(restore_arguments={
        "entity_id": "light.office", "rgb_color": "$prior.attributes.rgb_color",
    })

    instance_id = store.fire(rule=rule, registry=registry)
    assert instance_id is not None
    assert calls[-1] == ("set_light", {"entity_id": "light.office", "rgb_color": [0, 0, 255]})

    rule_store = MagicMock()
    rule_store.get.return_value = rule
    assert store.resolve(instance_id, registry=registry, rule_store=rule_store) is True

    assert calls[-1] == ("set_light", {"entity_id": "light.office", "rgb_color": [255, 255, 255]}), \
        "the light must go back to its real prior colour, not the colour the rule set"


def test_missing_placeholder_path_refuses_to_fire(tmp_path):
    """A template referencing a path the read_tool didn't return can't
    build a correct restore either -- same fail-closed treatment."""
    store = RuleInstanceStore(tmp_path / "rules.db")
    calls: list = []
    rule = _ha_shaped_rule(restore_arguments={"rgb_color": "$prior.attributes.nonexistent"})

    assert store.fire(rule=rule, registry=_ha_registry(calls)) is None
    assert [tool for tool, _ in calls] == ["get_state"]
