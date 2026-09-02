"""PRD.md §15 unit 30 / Appendix A.1: "Acknowledgment closes the loop."
This is the one path that exercises Phase B (threads/world model),
Phase C (salience's rule matching isn't directly hit, but the mechanism
it depends on is), G3 (rule instance reversal), and H (the dashboard)
together -- clicking "got it" must emit the same thread.acknowledged
observation a spoken acknowledgment would, closing the thread, resolving
any rule instance watching it, and restoring prior state."""

from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from argus.orchestrator import Orchestrator
from argus.proactive_engine import ProactiveEngine
from argus.tools.base import PermissionTier, Tool
from argus.ui import commands as ui_commands
from argus.ui.server import app


def _isolated_engine(tmp_path, monkeypatch) -> ProactiveEngine:
    monkeypatch.setattr("argus.config.settings.argus_data_dir", str(tmp_path))
    monkeypatch.setattr("argus.config.settings.spine_db_path", str(tmp_path / "spine.db"))
    orch = Orchestrator()
    return ProactiveEngine(orch, speak_fn=MagicMock(), interaction_lock=MagicMock())


# -- a fake reversible tool_call rule, for the end-to-end path --------

class _FakeLight:
    """A tiny in-memory stand-in for a real device -- read_tool reports
    its current color, the write tool changes it. Real enough to prove
    the restore call actually ran with the right arguments, not just
    that some resolution flag flipped."""

    def __init__(self):
        self.rgb_color = [255, 255, 255]  # starts white


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
    """Sets up and fires a rule that (a) turns the light blue and (b) is
    watched by `thread_id` via until_condition: user_acknowledged.
    Returns (rule_id, instance_id)."""
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


def test_click_to_restored_state_end_to_end(tmp_path, monkeypatch):
    """The whole point of unit 30: one test that goes from click to
    restored state."""
    engine = _isolated_engine(tmp_path, monkeypatch)
    light = _FakeLight()
    _register_light_tools(engine.orchestrator.tools, light)

    thread_id = engine.threads.open_commitment(text="build failed -- investigate")
    rule_id, instance_id = _fire_reversible_light_rule(engine, light, thread_id)

    # The rule fired: the light is blue now, not its original white.
    assert light.rgb_color == [0, 0, 255]
    assert engine.threads.get(thread_id).closed_ts is None
    assert engine.rule_instances.get(instance_id).state == "active"

    ui_commands.set_active_proactive_engine(engine)
    try:
        response = TestClient(app).post(f"/api/threads/{thread_id}/acknowledge")
    finally:
        ui_commands.set_active_proactive_engine(None)

    assert response.status_code == 200
    assert response.json() == {"ok": True}

    # 1. The click closes the thread.
    closed_thread = engine.threads.get(thread_id)
    assert closed_thread.closed_ts is not None

    # 2. The watching rule instance resolves.
    resolved_instance = engine.rule_instances.get(instance_id)
    assert resolved_instance.state == "resolved"

    # 3. Prior state is restored -- the light is back to white, not blue.
    assert light.rgb_color == [255, 255, 255]

    # 4. The exact spine observation Appendix A.1 specifies was recorded.
    acks = engine.spine.query(kinds=["thread.acknowledged"])
    assert len(acks) == 1
    assert acks[0].payload == {"thread_id": thread_id, "via": "ui"}

    # 5. The rule itself is untouched (still active, just no longer firing).
    assert engine.orchestrator.rule_store.get(rule_id).status == "active"


def test_spoken_and_clicked_acknowledgment_are_indistinguishable_downstream(tmp_path, monkeypatch):
    """Same mechanism, same observation shape, differing only in `via` --
    not a parallel path for the UI."""
    engine = _isolated_engine(tmp_path, monkeypatch)
    thread_a = engine.threads.open_commitment(text="thread a")
    thread_b = engine.threads.open_commitment(text="thread b")

    assert engine.acknowledge_thread(thread_a, via="ui") is True
    assert engine.acknowledge_thread(thread_b, via="voice") is True

    acks = {a.payload["thread_id"]: a for a in engine.spine.query(kinds=["thread.acknowledged"])}
    assert acks[thread_a].kind == acks[thread_b].kind == "thread.acknowledged"
    assert set(acks[thread_a].payload.keys()) == set(acks[thread_b].payload.keys()) == {"thread_id", "via"}
    assert acks[thread_a].payload["via"] == "ui"
    assert acks[thread_b].payload["via"] == "voice"
    # Both closed the same way -- via the same close_condition evaluation.
    assert engine.threads.get(thread_a).closed_ts is not None
    assert engine.threads.get(thread_b).closed_ts is not None


def test_acknowledge_unknown_thread_returns_false(tmp_path, monkeypatch):
    engine = _isolated_engine(tmp_path, monkeypatch)

    assert engine.acknowledge_thread(999_999, via="ui") is False


def test_acknowledge_endpoint_with_no_engine_running(monkeypatch):
    monkeypatch.setattr("argus.ui.commands._active_proactive_engine", None)
    client = TestClient(app)

    response = client.post("/api/threads/1/acknowledge")

    assert response.status_code == 200
    assert response.json() == {"ok": False, "error": "no engine running"}


def test_acknowledge_endpoint_unknown_thread_returns_ok_false(tmp_path, monkeypatch):
    engine = _isolated_engine(tmp_path, monkeypatch)
    ui_commands.set_active_proactive_engine(engine)
    try:
        response = TestClient(app).post("/api/threads/999999/acknowledge")
    finally:
        ui_commands.set_active_proactive_engine(None)

    assert response.json() == {"ok": False}


def test_dismissing_a_held_item_marks_it_dismissed_and_it_does_not_reappear(tmp_path, monkeypatch):
    engine = _isolated_engine(tmp_path, monkeypatch)
    item_id = engine.held.add(kind="mail.received", subject="a@x.com", text="fyi", score=0.4)
    assert item_id in [i.id for i in engine.held.pending()]

    ui_commands.set_active_proactive_engine(engine)
    try:
        response = TestClient(app).post(f"/api/held/{item_id}/dismiss")
    finally:
        ui_commands.set_active_proactive_engine(None)

    assert response.json() == {"ok": True}
    assert item_id not in [i.id for i in engine.held.pending()]


def test_dismiss_held_item_endpoint_with_no_engine_running(monkeypatch):
    monkeypatch.setattr("argus.ui.commands._active_proactive_engine", None)
    client = TestClient(app)

    response = client.post("/api/held/1/dismiss")

    assert response.status_code == 200
    assert response.json() == {"ok": False, "error": "no engine running"}


def test_state_reports_a_rule_as_firing_while_its_instance_is_active(tmp_path, monkeypatch):
    engine = _isolated_engine(tmp_path, monkeypatch)
    light = _FakeLight()
    _register_light_tools(engine.orchestrator.tools, light)
    thread_id = engine.threads.open_commitment(text="build failed")
    rule_id, instance_id = _fire_reversible_light_rule(engine, light, thread_id)

    ui_commands.set_active_proactive_engine(engine)
    try:
        before = TestClient(app).get("/api/state").json()
        [rule_row] = [r for r in before["rules"] if r["id"] == rule_id]
        assert rule_row["firing"] is True

        engine.acknowledge_thread(thread_id, via="ui")

        after = TestClient(app).get("/api/state").json()
    finally:
        ui_commands.set_active_proactive_engine(None)

    matching = [r for r in after["rules"] if r["id"] == rule_id]
    # Still an active rule, just no longer firing (the instance resolved).
    assert matching == [] or matching[0]["firing"] is False
