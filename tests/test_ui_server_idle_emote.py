from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from argus.idle_emote import _FALLBACK_SPECS
from argus.ui import commands as ui_commands
from argus.ui.server import app


def test_returns_a_fallback_when_no_router_is_registered(monkeypatch):
    monkeypatch.setattr("argus.ui.commands._active_router", None)
    client = TestClient(app)

    response = client.get("/api/idle_emote")

    assert response.json() in _FALLBACK_SPECS


def test_uses_the_registered_router_when_available(monkeypatch):
    from argus.llm.base import CompletionResult, Tier

    fake_router = MagicMock()
    fake_router.complete.return_value = CompletionResult(
        text='{"name": "cat ears", "parts": [{"type": "ring", "r": 0.1, "share": 0.2}]}',
        tier=Tier.LOCAL, model="test",
    )
    ui_commands.set_active_router(fake_router)
    try:
        client = TestClient(app)
        response = client.get("/api/idle_emote")
        assert response.json()["name"] == "cat ears"
        fake_router.complete.assert_called_once()
    finally:
        ui_commands.set_active_router(None)


def test_set_and_get_active_router_round_trip():
    sentinel = object()
    ui_commands.set_active_router(sentinel)
    try:
        assert ui_commands.get_active_router() is sentinel
    finally:
        ui_commands.set_active_router(None)
