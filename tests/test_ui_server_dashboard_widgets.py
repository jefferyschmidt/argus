"""PRD.md §15 unit 29: dashboard widgets. Extends ui/static/index.html
(§0 rule 3 -- extend, don't rewrite) with markup that renders purely
from GET /api/state. There's no JS test runner in this repo, so this
file covers what's actually testable from Python: the config plumbing
that bounds the polling cadence, and that the served page carries the
new markup/behavior without breaking anything that served before it."""

from fastapi.testclient import TestClient

from argus.ui.server import app


def test_dashboard_poll_seconds_is_exposed_in_config(monkeypatch):
    monkeypatch.setattr("argus.ui.server.settings.dashboard_poll_seconds", 7.5)
    client = TestClient(app)

    response = client.get("/api/config")

    assert response.json()["dashboard_poll_seconds"] == 7.5


def test_dashboard_poll_seconds_defaults_to_five():
    from argus.config import settings

    assert settings.dashboard_poll_seconds == 5.0


def test_index_page_still_serves_and_still_contains_the_existing_ui():
    """§0 rule 3: everything that worked before unit 29 must still work
    identically -- the face canvas, transcript, text input, and existing
    panels must still be present verbatim."""
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    text = response.text
    for marker in (
        'id="head"', 'id="transcriptBody"', 'id="textInputForm"',
        'id="pttBtn"', 'id="quietBtn"', 'id="pendingConfirms"',
        'id="voiceHeadline"', 'id="toolRows"', 'id="tierBadge"',
    ):
        assert marker in text, f"missing pre-existing element: {marker}"


def test_index_page_contains_the_new_dashboard_widgets_hidden_by_default():
    """A widget with no data does not appear -- every dashboard panel
    starts `hidden` in the markup itself, unhidden only by JS once
    /api/state actually has rows for it."""
    client = TestClient(app)

    text = client.get("/").text

    for panel_id in (
        "dashThreadsPanel", "dashMailPanel", "dashObligationsPanel", "dashDevicesPanel",
        "dashRulesPanel", "dashHealthPanel", "dashHeldPanel", "dashFocusPanel",
    ):
        assert f'id="{panel_id}" hidden' in text, f"{panel_id} must start hidden"
    assert 'id="dashEmptyNote" hidden' in text


def test_index_page_polls_state_no_faster_than_the_configured_interval():
    """The only setInterval driving /api/state must be gated by
    settings.dashboard_poll_seconds (via /api/config), not a hardcoded
    faster constant."""
    client = TestClient(app)

    text = client.get("/").text

    assert "startDashboardPolling(cfg.dashboard_poll_seconds" in text
    assert "setInterval(fetchState, Math.max(1, pollSeconds) * 1000)" in text
