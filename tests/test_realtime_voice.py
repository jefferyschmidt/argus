from unittest.mock import patch

from argus.tools import build_default_registry
from argus.voice.realtime import RealtimeVoiceLoop, _make_ui_confirmer


def test_realtime_session_uses_native_audio_vad_and_interruption(monkeypatch):
    listener = RealtimeVoiceLoop.__new__(RealtimeVoiceLoop)
    listener.tools = build_default_registry()
    monkeypatch.setattr("argus.voice.realtime.settings.openai_realtime_model", "gpt-realtime-2.1-mini")
    monkeypatch.setattr("argus.voice.realtime.settings.openai_realtime_voice", "marin")

    session = listener._session_config()

    assert session["output_modalities"] == ["audio"]
    assert any(tool["name"] == "list_dir" for tool in session["tools"])
    assert session["tool_choice"] == "auto"
    assert session["audio"]["input"]["turn_detection"]["interrupt_response"] is False
    assert session["audio"]["input"]["turn_detection"]["create_response"] is False
    assert session["audio"]["input"]["noise_reduction"]["type"] == "near_field"
    assert session["audio"]["input"]["transcription"]["model"] == "gpt-4o-mini-transcribe"
    assert session["audio"]["output"]["voice"] == "marin"


def test_accepts_an_externally_built_tool_registry():
    """ROADMAP.md Phase 1: a single tool registry shared across every
    consumer (chat, this realtime loop, and eventually the proactive
    engine) instead of each one silently building its own with no shared
    task-approval or cost-governor state."""
    shared = build_default_registry()
    with patch("argus.voice.realtime.settings.openai_api_key", "test-key"):
        loop = RealtimeVoiceLoop(tool_registry=shared)
    assert loop.tools is shared


def test_still_builds_its_own_registry_when_none_is_given():
    with patch("argus.voice.realtime.settings.openai_api_key", "test-key"):
        loop = RealtimeVoiceLoop()
    assert loop.tools is not None
    assert any(tool["name"] == "list_dir" for tool in loop.tools.schemas())


def test_realtime_prompt_is_accurate_about_available_tools():
    from argus.voice.realtime import _REALTIME_INSTRUCTIONS

    assert "they are available in this" in _REALTIME_INSTRUCTIONS
    assert "Tool results are authoritative" in _REALTIME_INSTRUCTIONS


def test_receiver_marks_an_unexpected_clean_socket_close_for_reconnect():
    listener = RealtimeVoiceLoop.__new__(RealtimeVoiceLoop)
    import threading

    listener._stop = threading.Event()
    listener._connection_lost = threading.Event()
    listener._connection_error = None

    listener._receive([])

    assert listener._connection_lost.is_set()
    assert listener._connection_error == "The voice connection closed."


def test_realtime_confirmation_uses_the_visual_console_not_terminal():
    confirmer = _make_ui_confirmer()

    with patch("argus.ui.commands.request_confirmation", return_value=9) as request, \
         patch("argus.ui.commands.wait_for_confirmation", return_value=True) as wait, \
         patch("argus.voice.realtime.ui_events.publish") as publish:
        assert confirmer("delete_email", {"uid": "42"}) is True

    request.assert_called_once_with("delete_email", {"uid": "42"})
    wait.assert_called_once_with(9, 45.0)
    assert publish.call_count == 2
