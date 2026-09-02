"""PRD.md §15 unit 28: GET /api/state. The governing constraint is that
the dashboard and voice interface are two projections of ONE world model
-- the server must reach the already-constructed ProactiveEngine
(ui_commands.get_active_proactive_engine(), the set_active_router
precedent applied here) rather than building its own WorldModel/
ThreadStore/SpineStore (P4, and P1: uvicorn serves this on its own
thread)."""

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from argus.ui import commands as ui_commands
from argus.ui.server import app, state


def test_returns_empty_but_shaped_json_with_no_engine_running(monkeypatch):
    """argus chat, or a bare UI preview, has no ProactiveEngine at all --
    this must be valid JSON with the same top-level shape as a live
    response, never a 500."""
    monkeypatch.setattr("argus.ui.commands._active_proactive_engine", None)
    client = TestClient(app)

    response = client.get("/api/state")

    assert response.status_code == 200
    body = response.json()
    assert body["engine_running"] is False
    assert body["open_threads"] == []
    assert body["horizon"] == []
    assert body["health"] == []
    assert body["held"] == []
    assert body["rules"] == []
    assert body["rhythms"] == {"baselines": {}}
    assert body["focus"] is None
    assert body["now"] is None


def test_empty_and_live_responses_share_the_same_top_level_keys():
    """Shape stability: a widget must be able to read the same keys
    whether or not an engine is running."""
    engine = _fake_engine()
    ui_commands.set_active_proactive_engine(engine)
    try:
        client = TestClient(app)
        live_keys = set(client.get("/api/state").json().keys())
    finally:
        ui_commands.set_active_proactive_engine(None)

    empty_keys = set(TestClient(app).get("/api/state").json().keys())
    assert live_keys == empty_keys


def _fake_engine():
    from argus.world.model import FocusState, RhythmSummary, WorldSnapshot

    engine = MagicMock()
    engine.world_model.snapshot.return_value = WorldSnapshot(
        now=__import__("datetime").datetime(2026, 9, 2, 10, 0, 0),
        focus=FocusState(title="editor.exe", minutes=12.0, confidence=1.0),
        open_threads=[], horizon=[], devices={}, health=[], rhythms=RhythmSummary(),
    )
    engine.held.pending.return_value = []
    engine.orchestrator.rule_store.list_active.return_value = []
    engine.rule_instances.list_active.return_value = []
    return engine


def test_returns_the_world_snapshot_held_items_and_active_rules():
    from argus.rules.store import Rule
    from argus.salience.held import HeldItem

    engine = _fake_engine()
    engine.held.pending.return_value = [
        HeldItem(id=1, created_ts=100.0, kind="mail.received", subject="a@x.com", text="new mail", score=0.4, thread_id=None, delivered_ts=None, dismissed_ts=None)
    ]
    engine.orchestrator.rule_store.list_active.return_value = [
        Rule(
            id=7, natural_language="suppress newsletters", source_utterance=None, kind="suppression",
            trigger={"kind": "mail.received", "filters": []}, conditions=[], action={"type": "suppress"},
            until_condition=None, group_name=None, status="active", authorization=None,
            created_ts=100.0, confirmed_ts=100.0, revoked_ts=None, hit_count=0, last_fired_ts=None, origin="user",
        )
    ]
    ui_commands.set_active_proactive_engine(engine)
    try:
        response = TestClient(app).get("/api/state")
    finally:
        ui_commands.set_active_proactive_engine(None)

    body = response.json()
    assert body["engine_running"] is True
    assert body["focus"] == {"title": "editor.exe", "minutes": 12.0, "confidence": 1.0}
    assert body["held"][0]["text"] == "new mail"
    assert body["rules"][0] == {
        "id": 7, "natural_language": "suppress newsletters", "kind": "suppression",
        "status": "active", "firing": False,
    }


def test_never_constructs_a_second_store():
    """P4 (and P1: this runs on the UI's own thread) -- the endpoint must
    reach the engine's already-constructed stores, never build its own."""
    engine = _fake_engine()
    ui_commands.set_active_proactive_engine(engine)
    try:
        with patch("argus.spine.store.SpineStore.__init__", side_effect=AssertionError("must not construct a SpineStore")), \
             patch("argus.world.threads.ThreadStore.__init__", side_effect=AssertionError("must not construct a ThreadStore")), \
             patch("argus.world.model.WorldModel.__init__", side_effect=AssertionError("must not construct a WorldModel")):
            response = TestClient(app).get("/api/state")
        assert response.status_code == 200
    finally:
        ui_commands.set_active_proactive_engine(None)


def test_honors_the_existing_snapshot_ttl_at_most_one_recompute_for_100_rapid_calls():
    """WorldModel.snapshot() already TTL-caches (§4.3) -- the endpoint
    must not add its own polling/recompute on top, just call it
    plainly."""
    from argus.spine.store import SpineStore
    from argus.world.model import WorldModel
    from argus.world.rhythms import RhythmStore
    from argus.world.threads import ThreadStore

    spine = SpineStore(":memory:")
    threads = MagicMock(spec=ThreadStore)
    threads.open_threads.return_value = []
    rhythms = MagicMock(spec=RhythmStore)
    rhythms.get.return_value = None
    world_model = WorldModel(spine=spine, threads=threads, rhythms=rhythms)

    calls = {"n": 0}
    real_build = world_model._build_snapshot

    def counting_build(now):
        calls["n"] += 1
        return real_build(now)

    world_model._build_snapshot = counting_build

    engine = MagicMock()
    engine.world_model = world_model
    engine.held.pending.return_value = []
    engine.orchestrator.rule_store.list_active.return_value = []
    ui_commands.set_active_proactive_engine(engine)
    try:
        for _ in range(100):
            state()
    finally:
        ui_commands.set_active_proactive_engine(None)

    assert calls["n"] == 1


def test_never_500s_with_no_engine(monkeypatch):
    monkeypatch.setattr("argus.ui.commands._active_proactive_engine", None)
    client = TestClient(app)

    response = client.get("/api/state")

    assert response.status_code == 200


def test_lists_are_capped_regardless_of_how_many_rows_exist():
    from argus.rules.store import Rule

    engine = _fake_engine()
    engine.orchestrator.rule_store.list_active.return_value = [
        Rule(
            id=i, natural_language=f"rule {i}", source_utterance=None, kind="preference",
            trigger={"kind": "mail.received", "filters": []}, conditions=[], action={"type": "boost", "amount": 0.1},
            until_condition=None, group_name=None, status="active", authorization=None,
            created_ts=100.0, confirmed_ts=100.0, revoked_ts=None, hit_count=0, last_fired_ts=None, origin="user",
        )
        for i in range(200)
    ]
    ui_commands.set_active_proactive_engine(engine)
    try:
        response = TestClient(app).get("/api/state")
    finally:
        ui_commands.set_active_proactive_engine(None)

    assert len(response.json()["rules"]) <= 50


def test_set_and_get_active_proactive_engine_round_trip():
    sentinel = object()
    ui_commands.set_active_proactive_engine(sentinel)
    try:
        assert ui_commands.get_active_proactive_engine() is sentinel
    finally:
        ui_commands.set_active_proactive_engine(None)
