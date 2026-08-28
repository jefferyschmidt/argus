from fastapi.testclient import TestClient

from argus.ui.server import app


def test_wake_word_model_is_none_when_local_engine_active(monkeypatch):
    """Reporting settings.wake_word_model unconditionally used to leave the
    console showing "hey_jarvis_v0.1" even once the local engine (which
    uses no trained model at all) became the default -- actively
    misleading about what's actually running."""
    monkeypatch.setattr("argus.ui.server.settings.wake_word_engine", "local")
    client = TestClient(app)

    response = client.get("/api/config")

    assert response.json()["wake_word_engine"] == "local"
    assert response.json()["wake_word_model"] is None


def test_wake_word_model_is_reported_when_openwakeword_engine_active(monkeypatch):
    monkeypatch.setattr("argus.ui.server.settings.wake_word_engine", "openwakeword")
    monkeypatch.setattr("argus.ui.server.settings.wake_word_model", "hey_jarvis_v0.1")
    client = TestClient(app)

    response = client.get("/api/config")

    assert response.json()["wake_word_engine"] == "openwakeword"
    assert response.json()["wake_word_model"] == "hey_jarvis_v0.1"
