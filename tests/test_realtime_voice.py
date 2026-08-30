import queue
import threading
from unittest.mock import patch

import numpy as np

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


def test_should_forward_mic_audio_respects_listening_paused():
    """Confirmed orphaned in realtime mode (ROADMAP.md Phase 2): the mic
    kept streaming to OpenAI regardless of "listening paused" in the
    console UI, unlike the pipeline loop's mic loop which already checks
    this."""
    listener = RealtimeVoiceLoop.__new__(RealtimeVoiceLoop)

    with patch("argus.voice.realtime.ui_commands.is_listening_paused", return_value=True):
        assert listener._should_forward_mic_audio() is False

    with patch("argus.voice.realtime.ui_commands.is_listening_paused", return_value=False):
        assert listener._should_forward_mic_audio() is True


def test_quiet_mode_suppresses_audio_playback_but_not_captions():
    """Confirmed orphaned in realtime mode (ROADMAP.md Phase 2): quiet mode
    wasn't checked at all here, so turning it on in the console had no
    effect on realtime-mode audio. Same contract as the pipeline loop's
    _speak_with_barge_in -- audio playback is skipped, but the caption/
    transcript events still publish."""
    import base64
    import json

    listener = RealtimeVoiceLoop.__new__(RealtimeVoiceLoop)
    listener._stop = threading.Event()
    listener._connection_lost = threading.Event()
    listener._connection_error = None
    listener._output = queue.Queue()
    event = json.dumps({
        "type": "response.output_audio.delta",
        "delta": base64.b64encode(b"\x00\x01\x02\x03").decode("ascii"),
    })

    with patch("argus.voice.realtime.ui_commands.is_quiet_mode", return_value=True), \
         patch("argus.voice.realtime.ui_events.publish"):
        listener._receive([event])

    assert listener._output.empty()


def test_normal_mode_still_plays_audio():
    import base64
    import json

    listener = RealtimeVoiceLoop.__new__(RealtimeVoiceLoop)
    listener._stop = threading.Event()
    listener._connection_lost = threading.Event()
    listener._connection_error = None
    listener._output = queue.Queue()
    event = json.dumps({
        "type": "response.output_audio.delta",
        "delta": base64.b64encode(b"\x00\x01\x02\x03").decode("ascii"),
    })

    with patch("argus.voice.realtime.ui_commands.is_quiet_mode", return_value=False), \
         patch("argus.voice.realtime.ui_events.publish"):
        listener._receive([event])

    assert not listener._output.empty()


def test_announce_returns_false_with_no_open_connection():
    """ROADMAP.md Phase 2: a proactive worker must get a clean False (to
    retry later, same as the pipeline loop's _pending_delivery pattern),
    not an exception, when nothing's connected yet."""
    loop = RealtimeVoiceLoop.__new__(RealtimeVoiceLoop)
    loop._socket = None
    loop._response_active = False
    loop._playback = np.empty(0, dtype="int16")
    loop._output = queue.Queue()
    loop._playback_lock = threading.Lock()

    assert loop.announce("you've got mail") is False


def test_announce_returns_false_while_audio_is_active():
    """Must not barge into an in-progress turn -- same non-blocking
    contract as VoiceLoop's interaction lock."""
    loop = RealtimeVoiceLoop.__new__(RealtimeVoiceLoop)
    loop._socket = object()
    loop._response_active = True
    loop._playback = np.empty(0, dtype="int16")
    loop._output = queue.Queue()
    loop._playback_lock = threading.Lock()

    assert loop.announce("you've got mail") is False


def test_announce_injects_a_conversation_item_when_idle():
    loop = RealtimeVoiceLoop.__new__(RealtimeVoiceLoop)
    fake_socket = object()
    loop._socket = fake_socket
    loop._response_active = False
    loop._playback = np.empty(0, dtype="int16")
    loop._output = queue.Queue()
    loop._playback_lock = threading.Lock()
    loop._send_lock = threading.Lock()
    sent = []
    loop._send = lambda socket, event: sent.append((socket, event))

    with patch("argus.voice.realtime.ui_events.publish"):
        result = loop.announce("you've got mail")

    assert result is True
    assert sent[0][0] is fake_socket
    assert sent[0][1]["type"] == "conversation.item.create"
    assert "you've got mail" in sent[0][1]["item"]["content"][0]["text"]
    assert sent[1][1] == {"type": "response.create"}


def test_realtime_loop_gets_a_proactive_engine():
    """ROADMAP.md Phase 2: realtime mode was confirmed to have zero
    proactive workers before this wiring existed."""
    with patch("argus.voice.realtime.settings.openai_api_key", "test-key"):
        loop = RealtimeVoiceLoop()

    assert loop.proactive is not None
    assert loop.proactive.email_watcher is not None
    assert loop.orchestrator is not None


def test_announce_lock_reflects_audio_activity():
    from argus.voice.realtime import _AnnounceLock

    loop = RealtimeVoiceLoop.__new__(RealtimeVoiceLoop)
    loop._socket = object()
    loop._response_active = False
    loop._playback = np.empty(0, dtype="int16")
    loop._output = queue.Queue()
    loop._playback_lock = threading.Lock()
    lock = _AnnounceLock(loop)

    assert lock.acquire() is True
    loop._response_active = True
    assert lock.acquire() is False
    lock.release()  # must not raise


def test_announce_lock_reflects_no_connection():
    from argus.voice.realtime import _AnnounceLock

    loop = RealtimeVoiceLoop.__new__(RealtimeVoiceLoop)
    loop._socket = None
    lock = _AnnounceLock(loop)

    assert lock.acquire() is False


def test_realtime_confirmation_uses_the_visual_console_not_terminal():
    confirmer = _make_ui_confirmer()

    with patch("argus.ui.commands.request_confirmation", return_value=9) as request, \
         patch("argus.ui.commands.wait_for_confirmation", return_value=True) as wait, \
         patch("argus.voice.realtime.ui_events.publish") as publish:
        assert confirmer("delete_email", {"uid": "42"}) is True

    request.assert_called_once_with("delete_email", {"uid": "42"})
    wait.assert_called_once_with(9, 45.0)
    assert publish.call_count == 2
