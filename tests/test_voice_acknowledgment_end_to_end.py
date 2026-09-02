"""PRD.md §15 unit 32 acceptance: "A voice acknowledgment resolves a
watching rule instance and restores prior state, exactly as the UI path
does." Mirrors test_ui_server_acknowledge.py's
test_click_to_restored_state_end_to_end, but through
maybe_acknowledge_spoken_thread (the shared function both voice loops
call) instead of the /api/threads/{id}/acknowledge endpoint -- same
underlying ProactiveEngine.acknowledge_thread(), same real
RuleInstanceStore/ThreadStore/SpineStore, proving the two paths really
do converge on one mechanism instead of two that happen to look alike."""

from unittest.mock import MagicMock

from argus.orchestrator import Orchestrator
from argus.proactive_engine import ProactiveEngine
from argus.tools.base import PermissionTier, Tool
from argus.voice.acknowledgment import maybe_acknowledge_spoken_thread


def _isolated_engine(tmp_path, monkeypatch) -> ProactiveEngine:
    monkeypatch.setattr("argus.config.settings.argus_data_dir", str(tmp_path))
    monkeypatch.setattr("argus.config.settings.spine_db_path", str(tmp_path / "spine.db"))
    orch = Orchestrator()
    return ProactiveEngine(orch, speak_fn=MagicMock(), interaction_lock=MagicMock())


class _FakeLight:
    def __init__(self):
        self.rgb_color = [255, 255, 255]


def _register_light_tools(registry, light: _FakeLight) -> None:
    def get_light(args: dict) -> dict:
        return {"state": "on", "attributes": {"rgb_color": light.rgb_color}}

    def set_light(args: dict) -> str:
        light.rgb_color = args["rgb_color"]
        return "ok"

    registry.register(Tool(
        name="get_light", description="x", input_schema={"type": "object", "properties": {}},
        tier=PermissionTier.ALLOW, handler=get_light,
    ))
    registry.register(Tool(
        name="set_light", description="x", input_schema={"type": "object", "properties": {}},
        tier=PermissionTier.ALLOW, handler=set_light,
    ))


def _fire_reversible_light_rule(engine: ProactiveEngine, light: _FakeLight, thread_id: int) -> tuple[int, int]:
    rule_id = engine.orchestrator.rule_store.propose(
        natural_language="Turn the light blue until acknowledged", kind="automation",
        trigger={"kind": "task.failed", "filters": []},
        action={
            "type": "tool_call", "tool": "set_light", "arguments": {"rgb_color": [0, 0, 255]},
            "reversible": True, "read_tool": "get_light", "read_arguments": {},
            "restore_arguments": {"rgb_color": "$prior.attributes.rgb_color"},
        },
        until_condition={"type": "user_acknowledged"},
    )
    engine.orchestrator.rule_store.confirm(rule_id)
    rule = engine.orchestrator.rule_store.get(rule_id)

    instance_id = engine.rule_instances.fire(rule=rule, registry=engine.orchestrator.tools, watched_thread_id=thread_id)
    assert instance_id is not None
    return rule_id, instance_id


def test_saying_got_it_resolves_a_watching_rule_instance_and_restores_prior_state(tmp_path, monkeypatch):
    engine = _isolated_engine(tmp_path, monkeypatch)
    light = _FakeLight()
    _register_light_tools(engine.orchestrator.tools, light)

    thread_id = engine.threads.open_commitment(text="build failed -- investigate")
    rule_id, instance_id = _fire_reversible_light_rule(engine, light, thread_id)
    assert light.rgb_color == [0, 0, 255]  # the rule fired -- blue, not white

    # Argus just spoke about this thread (SalienceDispatcher's own
    # bookkeeping is exercised elsewhere, in test_salience_dispatch.py --
    # set directly here since this test's focus is what happens AFTER
    # that tracking exists, not the tracking mechanism itself).
    engine.dispatcher.last_spoken_thread_id = thread_id
    engine.dispatcher.last_spoken_ts = 1_000_000.0

    closed = maybe_acknowledge_spoken_thread("got it", engine, now=1_000_010.0)

    assert closed is True
    assert engine.threads.get(thread_id).closed_ts is not None
    assert engine.rule_instances.get(instance_id).state == "resolved"
    assert light.rgb_color == [255, 255, 255]  # restored, not left blue
    assert rule_id == engine.orchestrator.rule_store.get(rule_id).id  # rule itself untouched

    acks = engine.spine.query(kinds=["thread.acknowledged"])
    assert len(acks) == 1
    assert acks[0].payload == {"thread_id": thread_id, "via": "voice"}


def test_a_held_not_spoken_item_is_never_eligible(tmp_path, monkeypatch):
    """§15 unit 32 acceptance: an item that was HELD rather than spoken
    is never eligible -- the thread was never actually announced, so
    "got it" has nothing to resolve."""
    engine = _isolated_engine(tmp_path, monkeypatch)
    light = _FakeLight()
    _register_light_tools(engine.orchestrator.tools, light)

    thread_id = engine.threads.open_commitment(text="build failed -- investigate")
    _fire_reversible_light_rule(engine, light, thread_id)
    assert light.rgb_color == [0, 0, 255]

    # Nothing was ever spoken -- last_spoken_thread_id stays None, the
    # same state a purely-held item leaves the dispatcher in.
    assert engine.dispatcher.last_spoken_thread_id is None

    closed = maybe_acknowledge_spoken_thread("got it", engine, now=1_000_010.0)

    assert closed is False
    assert engine.threads.get(thread_id).closed_ts is None
    assert light.rgb_color == [0, 0, 255]  # still blue -- nothing restored


def test_acknowledgment_after_the_window_expires_does_not_close_the_thread(tmp_path, monkeypatch):
    from argus.config import settings

    engine = _isolated_engine(tmp_path, monkeypatch)
    thread_id = engine.threads.open_commitment(text="reply to Julia")
    engine.dispatcher.last_spoken_thread_id = thread_id
    engine.dispatcher.last_spoken_ts = 1_000_000.0

    too_late = 1_000_000.0 + settings.acknowledgment_window_seconds + 1
    closed = maybe_acknowledge_spoken_thread("got it", engine, now=too_late)

    assert closed is False
    assert engine.threads.get(thread_id).closed_ts is None
