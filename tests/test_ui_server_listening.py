from fastapi.testclient import TestClient

from argus.ui import commands as ui_commands
from argus.ui.server import app


def test_listening_toggle_flips_state_and_reports_it():
    ui_commands.set_listening_paused(False)
    client = TestClient(app)

    response = client.post("/api/listening/toggle")

    assert response.json() == {"listening_paused": True}
    assert ui_commands.is_listening_paused() is True
    ui_commands.set_listening_paused(False)  # cleanup


def test_listening_status_reflects_current_state():
    ui_commands.set_listening_paused(True)
    client = TestClient(app)

    response = client.get("/api/listening")

    assert response.json() == {"listening_paused": True}
    ui_commands.set_listening_paused(False)  # cleanup
