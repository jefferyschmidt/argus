import queue
import threading
import time
from unittest.mock import MagicMock, patch

import numpy as np

from argus.tools import build_default_registry
from argus.voice.realtime import RealtimeVoiceLoop, _make_voice_confirmer


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


# -- PRD §16 unit 34: date/time/timezone grounding -----------------------

def test_session_config_includes_current_date_time_and_timezone():
    from datetime import datetime

    listener = RealtimeVoiceLoop.__new__(RealtimeVoiceLoop)
    listener.tools = build_default_registry()
    listener._last_grounding_refresh_ts = 0.0

    session = listener._session_config()

    assert "Current date/time:" in session["instructions"]
    # The machine's REAL timezone abbreviation, not a default/guess
    # (confirmed live: realtime mode previously hallucinated Asia/Kolkata
    # with no grounding at all to go on).
    assert datetime.now().astimezone().strftime("%Z") in session["instructions"]


def test_persona_instructions_are_unchanged_grounding_is_appended_around_them():
    from argus.voice.realtime import _REALTIME_INSTRUCTIONS

    listener = RealtimeVoiceLoop.__new__(RealtimeVoiceLoop)
    listener.tools = build_default_registry()
    listener._last_grounding_refresh_ts = 0.0

    session = listener._session_config()

    assert session["instructions"].startswith(_REALTIME_INSTRUCTIONS)


def test_session_config_includes_user_location_when_set(monkeypatch):
    monkeypatch.setattr("argus.voice.realtime.settings.user_location", "Boston, MA")
    listener = RealtimeVoiceLoop.__new__(RealtimeVoiceLoop)
    listener.tools = build_default_registry()
    listener._last_grounding_refresh_ts = 0.0

    session = listener._session_config()

    assert "User's location: Boston, MA" in session["instructions"]


def test_session_config_omits_user_location_when_unset(monkeypatch):
    monkeypatch.setattr("argus.voice.realtime.settings.user_location", "")
    listener = RealtimeVoiceLoop.__new__(RealtimeVoiceLoop)
    listener.tools = build_default_registry()
    listener._last_grounding_refresh_ts = 0.0

    session = listener._session_config()

    assert "User's location" not in session["instructions"]


def test_grounding_is_recomputed_on_every_session_config_call_not_cached():
    """Regenerated at each connect (including reconnects), never frozen
    at import or construction time -- proven here by watching
    _time_grounding() actually get called fresh each time, not by
    inspecting the code."""
    listener = RealtimeVoiceLoop.__new__(RealtimeVoiceLoop)
    listener.tools = build_default_registry()
    listener._last_grounding_refresh_ts = 0.0
    grounding = MagicMock(side_effect=["\n\nCurrent date/time: Monday", "\n\nCurrent date/time: Tuesday"])

    with patch("argus.voice.realtime._time_grounding", grounding):
        first = listener._session_config()["instructions"]
        second = listener._session_config()["instructions"]

    assert "Monday" in first
    assert "Tuesday" in second
    assert grounding.call_count == 2


def test_session_config_updates_the_last_refresh_timestamp():
    listener = RealtimeVoiceLoop.__new__(RealtimeVoiceLoop)
    listener.tools = build_default_registry()
    listener._last_grounding_refresh_ts = 0.0

    before = time.time()
    listener._session_config()

    assert listener._last_grounding_refresh_ts >= before


def test_maybe_refresh_time_grounding_sends_nothing_when_fresh():
    listener = RealtimeVoiceLoop.__new__(RealtimeVoiceLoop)
    listener._last_grounding_refresh_ts = time.time()
    sent = []
    listener._send = lambda socket, event: sent.append(event)

    listener._maybe_refresh_time_grounding("fake-socket")

    assert sent == []


def test_maybe_refresh_time_grounding_sends_a_lightweight_update_when_stale(monkeypatch):
    """Long-session staleness (§16 unit 34): a session can run for
    hours, so the grounding sent at connect drifts. This is what keeps
    clock math honest without waiting for a reconnect."""
    monkeypatch.setattr("argus.voice.realtime.settings.realtime_time_refresh_seconds", 300.0)
    listener = RealtimeVoiceLoop.__new__(RealtimeVoiceLoop)
    listener._last_grounding_refresh_ts = time.time() - 301.0
    sent = []
    listener._send = lambda socket, event: sent.append(event)

    listener._maybe_refresh_time_grounding("fake-socket")

    assert len(sent) == 1
    assert sent[0]["type"] == "session.update"
    # Lightweight: only instructions, not a full _session_config() resend
    # re-declaring tools/audio config that hasn't changed.
    assert set(sent[0]["session"].keys()) == {"instructions"}
    assert "Current date/time:" in sent[0]["session"]["instructions"]
    assert listener._last_grounding_refresh_ts > time.time() - 5


def test_maybe_refresh_time_grounding_is_called_on_a_user_turn_when_stale(monkeypatch):
    """Wired into the same transcript branch as unit 32's acknowledgment
    check -- fires on the user's own turns, not on a separate timer."""
    import json

    monkeypatch.setattr("argus.voice.realtime.settings.realtime_time_refresh_seconds", 300.0)
    loop = RealtimeVoiceLoop.__new__(RealtimeVoiceLoop)
    loop._stop = threading.Event()
    loop._connection_lost = threading.Event()
    loop._connection_error = None
    loop._speech_lock = threading.Lock()
    loop._input_had_transcript = False
    loop._resume_timer = None
    loop._response_active = False
    loop._playback = np.empty(0, dtype="int16")
    loop._output = queue.Queue()
    loop._playback_lock = threading.Lock()
    loop._send_lock = threading.Lock()
    loop._last_expression = None
    loop._last_grounding_refresh_ts = time.time() - 301.0
    loop.tools = MagicMock()
    loop.proactive = None
    sent = []
    loop._send = lambda socket, event: sent.append(event)
    event = json.dumps({
        "type": "conversation.item.input_audio_transcription.completed",
        "transcript": "what's on my calendar today",
    })

    with patch("argus.voice.realtime.ui_commands.is_voice_confirmation_active", return_value=False), \
         patch("argus.voice.realtime.ui_events.publish"):
        loop._receive([event])

    assert any(e.get("type") == "session.update" for e in sent)

    assert loop._last_grounding_refresh_ts > time.time() - 5


def test_realtime_time_refresh_seconds_defaults_to_300():
    from argus.config import settings

    assert settings.realtime_time_refresh_seconds == 300.0


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


# -- PRD §16 unit 33: realtime must use the Orchestrator's full registry --

def test_tools_is_the_same_object_as_the_orchestrators_registry():
    """The fix in one sentence: self.tools = self.orchestrator.tools, not
    the other way around."""
    with patch("argus.voice.realtime.settings.openai_api_key", "test-key"):
        loop = RealtimeVoiceLoop()
    assert loop.tools is loop.orchestrator.tools


def test_a_supplied_tool_registry_is_shared_with_the_orchestrator_not_duplicated():
    shared = build_default_registry()
    with patch("argus.voice.realtime.settings.openai_api_key", "test-key"):
        loop = RealtimeVoiceLoop(tool_registry=shared)
    assert loop.tools is shared
    assert loop.orchestrator.tools is shared


def test_only_one_registry_is_constructed_per_realtime_voice_loop():
    """The whole reason the old (bad) wiring existed: commit cf612fd
    fixed realtime spawning duplicate MCP subprocesses by sharing ONE
    registry. This fix must not let that regress -- verified by counting
    actual build_default_registry() calls, not just by inspection."""
    real_build = build_default_registry
    counter = MagicMock(side_effect=real_build)
    with patch("argus.voice.realtime.settings.openai_api_key", "test-key"), \
         patch("argus.orchestrator.build_default_registry", counter):
        RealtimeVoiceLoop()

    counter.assert_called_once()


def test_realtime_registry_has_the_full_capability_set():
    """Confirmed live as the actual bug: asked to set a standing timezone
    rule, Argus reported having no rules engine at all -- because the
    old bare registry never got rule_store/spine/task_runner/
    decision_log/router, so none of the tools that depend on them were
    registered."""
    with patch("argus.voice.realtime.settings.openai_api_key", "test-key"):
        loop = RealtimeVoiceLoop()

    for name in ("remember_preference", "list_rules", "revoke_rule", "query_timeline", "compose_document"):
        assert name in loop.tools._tools, f"{name} missing from realtime mode's registry"


def test_standing_authorizations_are_wired_in_realtime_mode():
    with patch("argus.voice.realtime.settings.openai_api_key", "test-key"):
        loop = RealtimeVoiceLoop()

    assert loop.tools.authorization_checker is not None


def test_session_config_advertises_the_full_tool_set():
    with patch("argus.voice.realtime.settings.openai_api_key", "test-key"):
        loop = RealtimeVoiceLoop()

    advertised = {tool["name"] for tool in loop._session_config()["tools"]}
    for name in ("remember_preference", "list_rules", "query_timeline", "compose_document"):
        assert name in advertised


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


def test_create_response_or_defer_sends_immediately_when_idle():
    loop = RealtimeVoiceLoop.__new__(RealtimeVoiceLoop)
    loop._response_active = False
    loop._pending_create_after_cancel = False
    sent = []
    loop._send = lambda socket, event: sent.append(event)

    loop._create_response_or_defer("fake-socket")

    assert sent == [{"type": "response.create"}]
    assert loop._pending_create_after_cancel is False


def test_create_response_or_defer_cancels_and_defers_when_active():
    """Confirmed live as a real bug (seen in production): sending
    response.create immediately after response.cancel races the server --
    OpenAI processes the cancel asynchronously, so the create can arrive
    before the cancellation actually takes effect, and the API rejects it:
    "Conversation already has an active response in progress... Wait
    until the response is finished before creating a new one." """
    loop = RealtimeVoiceLoop.__new__(RealtimeVoiceLoop)
    loop._response_active = True
    loop._pending_create_after_cancel = False
    sent = []
    loop._send = lambda socket, event: sent.append(event)

    loop._create_response_or_defer("fake-socket")

    assert sent == [{"type": "response.cancel"}]  # NOT response.create yet
    assert loop._pending_create_after_cancel is True


def test_deferred_create_fires_only_once_response_done_confirms_it():
    """The deferred response.create must wait for the server's own
    confirmation that the previous response is actually finished (this
    event IS that confirmation), not fire eagerly."""
    import json
    import threading

    loop = RealtimeVoiceLoop.__new__(RealtimeVoiceLoop)
    loop._stop = threading.Event()
    loop._connection_lost = threading.Event()
    loop._connection_error = None
    loop._transcript = []
    loop._output_captioned = True
    loop._pending_calls = []
    loop._pending_create_after_cancel = True
    loop._pending_create_instructions = None
    sent = []
    loop._send = lambda socket, event: sent.append(event)

    with patch("argus.voice.realtime.ui_events.publish"):
        loop._receive([json.dumps({"type": "response.done"})])

    assert sent == [{"type": "response.create"}]
    assert loop._pending_create_after_cancel is False


def test_deferred_create_does_not_fire_on_error_to_avoid_a_retry_loop():
    """Confirmed as the real risk this guards against: retrying a deferred
    create straight into an error state turns one real bug into a
    self-sustaining error loop instead of just settling."""
    import json
    import threading

    loop = RealtimeVoiceLoop.__new__(RealtimeVoiceLoop)
    loop._stop = threading.Event()
    loop._connection_lost = threading.Event()
    loop._connection_error = None
    loop._transcript = []
    loop._output_captioned = True
    loop._pending_calls = []
    loop._pending_create_after_cancel = True
    sent = []
    loop._send = lambda socket, event: sent.append(event)

    with patch("argus.voice.realtime.ui_events.publish"):
        loop._receive([json.dumps({"type": "error", "error": {"message": "boom"}})])

    assert sent == []
    assert loop._pending_create_after_cancel is False


def test_deferred_create_does_not_double_fire_when_tools_are_pending():
    """_run_pending_tools sends its own response.create once tool results
    are in -- a deferred create firing too would be a second, redundant
    one."""
    import json
    import threading

    loop = RealtimeVoiceLoop.__new__(RealtimeVoiceLoop)
    loop._stop = threading.Event()
    loop._connection_lost = threading.Event()
    loop._connection_error = None
    loop._transcript = []
    loop._output_captioned = True
    loop._pending_calls = [{"name": "some_tool", "call_id": "1", "arguments": "{}"}]
    loop._pending_create_after_cancel = True
    loop.tools = MagicMock()
    loop.tools._tools = {}
    loop.tools.execute.return_value = "ok"
    sent = []
    loop._send = lambda socket, event: sent.append(event)

    # Tool execution now runs on a background thread (a CONFIRM-tier
    # tool's voice confirmer blocks waiting for a transcript event that
    # only the receive loop itself can deliver -- see
    # _ask_voice_confirmation -- so it can't run synchronously on that
    # same thread without deadlocking). Join whatever _receive spawns
    # before asserting on it.
    before = set(threading.enumerate())
    with patch("argus.voice.realtime.ui_events.publish"):
        loop._receive([json.dumps({"type": "response.done"})])
    for t in set(threading.enumerate()) - before:
        t.join(timeout=2)

    # _run_pending_tools's own response.create is the only one -- no
    # second, separately-triggered create from the deferred flag.
    assert sent.count({"type": "response.create"}) == 1
    assert loop._pending_create_after_cancel is False


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


def test_submit_text_message_injects_a_real_user_turn():
    """Confirmed orphaned in realtime mode (ROADMAP.md Phase 2): both the
    console text box and the Telegram bridge push onto a queue nothing
    read in this mode -- a sent message just silently vanished."""
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
        result = loop.submit_text_message("what's the weather")

    assert result is True
    assert sent[0][1]["type"] == "conversation.item.create"
    assert sent[0][1]["item"]["role"] == "user"
    assert sent[0][1]["item"]["content"][0]["text"] == "what's the weather"
    assert sent[1][1] == {"type": "response.create"}


def test_submit_text_message_returns_false_with_no_connection():
    loop = RealtimeVoiceLoop.__new__(RealtimeVoiceLoop)
    loop._socket = None

    with patch("argus.voice.realtime.ui_events.publish"):
        assert loop.submit_text_message("hello") is False


def test_submit_text_message_during_pending_confirmation_answers_it_instead_of_a_new_turn():
    """Same bug class pipeline mode's confirm.py already fixed: a typed
    answer sent while a voice confirmation is pending must resolve that
    confirmation, not start a whole new conversational turn."""
    loop = RealtimeVoiceLoop.__new__(RealtimeVoiceLoop)
    loop._socket = object()
    sent = []
    loop._send = lambda socket, event: sent.append(event)

    with patch("argus.voice.realtime.ui_commands.is_voice_confirmation_active", return_value=True), \
         patch("argus.voice.realtime.ui_commands.submit_confirmation_answer") as submit, \
         patch("argus.voice.realtime.ui_events.publish"):
        result = loop.submit_text_message("yes")

    assert result is True
    submit.assert_called_once_with("yes")
    assert sent == []


def test_text_input_worker_retries_until_delivered():
    loop = RealtimeVoiceLoop.__new__(RealtimeVoiceLoop)
    loop._stop = threading.Event()
    calls = {"n": 0}

    def fake_get_text_message(timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return "hello there"
        loop._stop.set()
        return None

    attempts = {"n": 0}

    def fake_submit(text):
        attempts["n"] += 1
        if attempts["n"] < 3:
            return False
        loop._stop.set()
        return True

    loop.submit_text_message = fake_submit

    with patch("argus.voice.realtime.ui_commands.get_text_message", side_effect=fake_get_text_message), \
         patch("argus.voice.realtime.time.sleep"):
        loop._text_input_worker()

    assert attempts["n"] == 3


def test_text_input_worker_gives_up_and_toasts_after_max_retries():
    loop = RealtimeVoiceLoop.__new__(RealtimeVoiceLoop)
    loop._stop = threading.Event()
    calls = {"n": 0}

    def fake_get_text_message(timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return "hello there"
        loop._stop.set()
        return None

    loop.submit_text_message = lambda text: False

    with patch("argus.voice.realtime.ui_commands.get_text_message", side_effect=fake_get_text_message), \
         patch("argus.voice.realtime.time.sleep"), \
         patch("argus.voice.realtime.ui_events.publish") as publish:
        loop._text_input_worker()

    toasts = [c.args[0] for c in publish.call_args_list if c.args[0].get("type") == "toast"]
    assert len(toasts) == 1


def test_direct_expression_request_triggers_the_face_in_realtime_mode():
    """Confirmed orphaned in realtime mode (ROADMAP.md Phase 2): a direct
    request ("show me you're happy") never reached the face at all --
    this mode's system prompt carries no EXPRESSION: marker protocol for
    the audio model to (unreliably) remember, so it has to be matched
    deterministically, same as the pipeline orchestrator does."""
    import json

    listener = RealtimeVoiceLoop.__new__(RealtimeVoiceLoop)
    listener._stop = threading.Event()
    listener._connection_lost = threading.Event()
    listener._connection_error = None
    listener._speech_lock = threading.Lock()
    listener._input_had_transcript = False
    listener._resume_timer = None
    listener._barge_in_timer = None
    listener._response_active = False
    listener._playback = np.empty(0, dtype="int16")
    listener._output = queue.Queue()
    listener._playback_lock = threading.Lock()
    listener._send_lock = threading.Lock()
    listener._last_expression = None
    listener.tools = MagicMock()
    event = json.dumps({
        "type": "conversation.item.input_audio_transcription.completed",
        "transcript": "show me your happy face",
    })

    with patch("argus.voice.realtime.ui_events.publish") as publish:
        listener._receive([event])

    assert listener._last_expression == "happy"
    expression_events = [c.args[0] for c in publish.call_args_list if c.args[0].get("type") == "expression"]
    assert expression_events == [{"type": "expression", "value": "happy"}]


def test_realtime_loop_gets_a_proactive_engine():
    """ROADMAP.md Phase 2: realtime mode was confirmed to have zero
    proactive workers before this wiring existed."""
    with patch("argus.voice.realtime.settings.openai_api_key", "test-key"):
        loop = RealtimeVoiceLoop()

    assert loop.proactive is not None
    assert loop.proactive.email_watcher is not None
    assert loop.orchestrator is not None


def test_orchestrator_shares_the_same_tool_registry_not_a_second_one():
    """Confirmed live as a real resource-doubling bug: Orchestrator builds
    its own registry by default, so leaving this unset spawned TWO
    separate headless-browser subprocesses for one realtime session with
    Playwright MCP enabled (one for self.tools, a second wasted one buried
    inside Orchestrator's own construction) -- same doubling for any other
    enabled MCP server (GitHub, Zapier, Home Assistant, Figma)."""
    with patch("argus.voice.realtime.settings.openai_api_key", "test-key"):
        loop = RealtimeVoiceLoop()

    assert loop.orchestrator.tools is loop.tools


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


def test_realtime_confirmation_accepts_a_spoken_yes_without_touching_the_ui():
    loop = RealtimeVoiceLoop.__new__(RealtimeVoiceLoop)
    loop._ask_voice_confirmation = MagicMock(return_value=True)
    confirmer = _make_voice_confirmer(loop)

    with patch("argus.ui.commands.request_confirmation", return_value=11) as request, \
         patch("argus.ui.commands.wait_for_confirmation") as wait, \
         patch("argus.ui.commands.resolve_confirmation") as resolve, \
         patch("argus.voice.realtime.ui_events.publish"):
        assert confirmer("delete_email", {"uid": "7"}) is True

    request.assert_called_once_with("delete_email", {"uid": "7"})
    loop._ask_voice_confirmation.assert_called_once()
    wait.assert_not_called()
    resolve.assert_called_once_with(11, True)


def test_realtime_confirmation_declines_on_a_spoken_no():
    loop = RealtimeVoiceLoop.__new__(RealtimeVoiceLoop)
    loop._ask_voice_confirmation = MagicMock(return_value=False)
    confirmer = _make_voice_confirmer(loop)

    with patch("argus.ui.commands.request_confirmation", return_value=12), \
         patch("argus.ui.commands.wait_for_confirmation") as wait, \
         patch("argus.ui.commands.resolve_confirmation") as resolve, \
         patch("argus.voice.realtime.ui_events.publish"):
        assert confirmer("delete_email", {"uid": "8"}) is False

    wait.assert_not_called()
    resolve.assert_called_once_with(12, False)


def test_realtime_confirmation_falls_back_to_the_console_after_two_unclear_voice_tries():
    loop = RealtimeVoiceLoop.__new__(RealtimeVoiceLoop)
    loop._ask_voice_confirmation = MagicMock(return_value=None)
    confirmer = _make_voice_confirmer(loop)

    with patch("argus.ui.commands.request_confirmation", return_value=9) as request, \
         patch("argus.ui.commands.wait_for_confirmation", return_value=True) as wait, \
         patch("argus.ui.commands.resolve_confirmation") as resolve, \
         patch("argus.voice.realtime.ui_events.publish") as publish:
        assert confirmer("delete_email", {"uid": "42"}) is True

    request.assert_called_once_with("delete_email", {"uid": "42"})
    assert loop._ask_voice_confirmation.call_count == 2
    wait.assert_called_once_with(9, 45.0)
    resolve.assert_not_called()
    assert publish.call_count == 2


def test_ask_voice_confirmation_reads_a_spoken_yes(monkeypatch):
    loop = RealtimeVoiceLoop.__new__(RealtimeVoiceLoop)
    loop._socket = object()
    loop._send = MagicMock()
    loop._create_response_or_defer = MagicMock()
    # Unit 24: the real audio-wait loop runs before listening starts --
    # give it real (idle) playback state so it drains instantly instead of
    # spinning for the full speak timeout.
    loop._playback_lock = threading.Lock()
    loop._playback = np.empty(0, dtype="int16")
    loop._output = queue.Queue()
    loop._response_active = False
    monkeypatch.setattr("argus.voice.realtime.settings.voice_confirm_speak_timeout_seconds", 0.0)

    with patch("argus.voice.realtime.ui_commands.get_confirmation_answer", return_value="yeah go ahead"), \
         patch("argus.voice.realtime.ui_commands.set_voice_confirmation_active") as set_active, \
         patch("argus.voice.realtime.ui_events.publish"):
        result = loop._ask_voice_confirmation("May I delete email? Say yes or no.")

    assert result is True
    # Unit 24a: the prompt rides as a response-scoped `instructions`
    # override on the deferred create, never as a conversation item --
    # and _send is never called directly by this function at all.
    loop._create_response_or_defer.assert_called_once_with(
        loop._socket,
        instructions='Say exactly this to the user, word for word, then stop: '
                      '"May I delete email? Say yes or no."',
    )
    loop._send.assert_not_called()
    # Must be active while listening and cleared afterward, even on the
    # success path -- a stuck-active flag would divert every later
    # transcript into the confirmation channel forever.
    assert set_active.call_args_list == [((True,),), ((False,),)]


def test_ask_voice_confirmation_reads_a_spoken_no(monkeypatch):
    loop = RealtimeVoiceLoop.__new__(RealtimeVoiceLoop)
    loop._socket = object()
    loop._send = MagicMock()
    loop._create_response_or_defer = MagicMock()
    loop._playback_lock = threading.Lock()
    loop._playback = np.empty(0, dtype="int16")
    loop._output = queue.Queue()
    loop._response_active = False
    monkeypatch.setattr("argus.voice.realtime.settings.voice_confirm_speak_timeout_seconds", 0.0)

    with patch("argus.voice.realtime.ui_commands.get_confirmation_answer", return_value="no, don't"), \
         patch("argus.voice.realtime.ui_commands.set_voice_confirmation_active"), \
         patch("argus.voice.realtime.ui_events.publish"):
        result = loop._ask_voice_confirmation("May I delete email? Say yes or no.")

    assert result is False


def test_ask_voice_confirmation_returns_none_on_silence_or_unclear_answer(monkeypatch):
    loop = RealtimeVoiceLoop.__new__(RealtimeVoiceLoop)
    loop._socket = object()
    loop._send = MagicMock()
    loop._create_response_or_defer = MagicMock()
    loop._playback_lock = threading.Lock()
    loop._playback = np.empty(0, dtype="int16")
    loop._output = queue.Queue()
    loop._response_active = False
    monkeypatch.setattr("argus.voice.realtime.settings.voice_confirm_speak_timeout_seconds", 0.0)

    with patch("argus.voice.realtime.ui_commands.get_confirmation_answer", return_value=None), \
         patch("argus.voice.realtime.ui_commands.set_voice_confirmation_active"), \
         patch("argus.voice.realtime.ui_events.publish"):
        assert loop._ask_voice_confirmation("May I delete email? Say yes or no.") is None

    with patch("argus.voice.realtime.ui_commands.get_confirmation_answer", return_value="what was that"), \
         patch("argus.voice.realtime.ui_commands.set_voice_confirmation_active"), \
         patch("argus.voice.realtime.ui_events.publish"):
        assert loop._ask_voice_confirmation("May I delete email? Say yes or no.") is None


def test_ask_voice_confirmation_returns_none_with_no_open_connection():
    loop = RealtimeVoiceLoop.__new__(RealtimeVoiceLoop)
    loop._socket = None

    assert loop._ask_voice_confirmation("May I delete email? Say yes or no.") is None


def test_ask_voice_confirmation_waits_for_the_question_audio_to_finish_before_listening(monkeypatch):
    """Unit 24 acceptance: the answer window does not begin while
    _audio_is_active() is true. Confirmed live: the old fixed window
    opened the instant the question was SENT, before OpenAI had spoken a
    word of it -- streaming the audio takes ~3-5s on its own, so both
    retry attempts timed out even when the user did answer."""
    loop = RealtimeVoiceLoop.__new__(RealtimeVoiceLoop)
    loop._socket = object()
    loop._send = MagicMock()
    loop._create_response_or_defer = MagicMock()
    monkeypatch.setattr("argus.voice.realtime.settings.voice_confirm_speak_timeout_seconds", 5.0)
    monkeypatch.setattr("argus.voice.realtime.settings.voice_confirm_listen_seconds", 10.0)

    # Audio starts active (question playing) and stays active for a few
    # polls before finishing.
    active_sequence = iter([True, True, True, False])
    loop._audio_is_active = MagicMock(side_effect=lambda: next(active_sequence, False))

    calls = []

    def fake_get_confirmation_answer(timeout):
        calls.append(("listen", timeout))
        return "yes"

    with patch("argus.voice.realtime.ui_commands.get_confirmation_answer", side_effect=fake_get_confirmation_answer), \
         patch("argus.voice.realtime.ui_commands.set_voice_confirmation_active"), \
         patch("argus.voice.realtime.ui_events.publish"), \
         patch("argus.voice.realtime.time.sleep"):
        result = loop._ask_voice_confirmation("May I delete email? Say yes or no.")

    assert result is True
    # Listening only started once _audio_is_active() had gone False, using
    # the configured listen window -- not the speak-timeout value, and not
    # started any earlier while audio was still active.
    assert calls == [("listen", 10.0)]
    assert loop._audio_is_active.call_count == 4


def test_ask_voice_confirmation_gives_up_after_speak_timeout_if_audio_never_finishes(monkeypatch):
    """Unit 24 acceptance: a generation that never produces audio still
    gives up after settings.voice_confirm_speak_timeout_seconds and does
    not hang the confirmer or the tool call behind it."""
    loop = RealtimeVoiceLoop.__new__(RealtimeVoiceLoop)
    loop._socket = object()
    loop._send = MagicMock()
    loop._create_response_or_defer = MagicMock()
    monkeypatch.setattr("argus.voice.realtime.settings.voice_confirm_speak_timeout_seconds", 0.01)
    monkeypatch.setattr("argus.voice.realtime.settings.voice_confirm_listen_seconds", 10.0)
    loop._audio_is_active = MagicMock(return_value=True)  # never finishes

    with patch("argus.voice.realtime.ui_commands.get_confirmation_answer", return_value="yes") as get_answer, \
         patch("argus.voice.realtime.ui_commands.set_voice_confirmation_active"), \
         patch("argus.voice.realtime.ui_events.publish"):
        result = loop._ask_voice_confirmation("May I delete email? Say yes or no.")

    get_answer.assert_called_once_with(timeout=10.0)
    assert result is True


def test_ask_voice_confirmation_never_sends_a_conversation_item(monkeypatch):
    """Unit 24a acceptance: the prompt travels only in a response-scoped
    `instructions` field -- never a conversation.item.create. That kind of
    item persists for the whole session, so one injected per confirmation
    attempt left the model under a standing "say this sentence" instruction
    it kept obeying on every later, unrelated turn -- confirmed live from
    data/events/events-2026-09-02.jsonl, unrecoverable without a restart.
    This drives the real _create_response_or_defer (not a mock) so the
    actual wire event is inspected end to end."""
    loop = RealtimeVoiceLoop.__new__(RealtimeVoiceLoop)
    loop._socket = object()
    loop._response_active = False
    loop._pending_create_after_cancel = False
    loop._pending_create_instructions = None
    loop._playback_lock = threading.Lock()
    loop._playback = np.empty(0, dtype="int16")
    loop._output = queue.Queue()
    monkeypatch.setattr("argus.voice.realtime.settings.voice_confirm_speak_timeout_seconds", 0.0)
    sent = []
    loop._send = lambda socket, event: sent.append(event)

    with patch("argus.voice.realtime.ui_commands.get_confirmation_answer", return_value="yes"), \
         patch("argus.voice.realtime.ui_commands.set_voice_confirmation_active"), \
         patch("argus.voice.realtime.ui_events.publish"):
        result = loop._ask_voice_confirmation("May I delete email? Say yes or no.")

    assert result is True
    assert not any(event["type"] == "conversation.item.create" for event in sent)
    assert sent == [{
        "type": "response.create",
        "response": {
            "instructions": 'Say exactly this to the user, word for word, then stop: '
                             '"May I delete email? Say yes or no."',
        },
    }]


def test_ask_voice_confirmation_defers_instructions_through_a_pending_cancel(monkeypatch):
    """Same wire-level proof as above, but through the cancel-then-defer
    path (a response was already active): the instructions must still
    reach the eventual response.create, not get dropped or leak as a
    conversation item."""
    import json

    loop = RealtimeVoiceLoop.__new__(RealtimeVoiceLoop)
    loop._socket = object()
    loop._response_active = True
    loop._pending_create_after_cancel = False
    loop._pending_create_instructions = None
    loop._playback_lock = threading.Lock()
    loop._playback = np.empty(0, dtype="int16")
    loop._output = queue.Queue()
    # _response_active stays True through this synchronous flow (nothing
    # here plays the role of the real receive thread flipping it back to
    # False), so the audio-wait loop would otherwise spin for the full
    # speak timeout -- cap it at 0 since this test isn't exercising that
    # wait itself.
    monkeypatch.setattr("argus.voice.realtime.settings.voice_confirm_speak_timeout_seconds", 0.0)
    sent = []
    loop._send = lambda socket, event: sent.append(event)

    with patch("argus.voice.realtime.ui_commands.get_confirmation_answer", return_value=None), \
         patch("argus.voice.realtime.ui_commands.set_voice_confirmation_active"), \
         patch("argus.voice.realtime.ui_events.publish"):
        loop._ask_voice_confirmation("May I delete email? Say yes or no.")

    assert sent == [{"type": "response.cancel"}]
    assert not any(event["type"] == "conversation.item.create" for event in sent)
    assert loop._pending_create_after_cancel is True
    assert loop._pending_create_instructions == (
        'Say exactly this to the user, word for word, then stop: '
        '"May I delete email? Say yes or no."'
    )

    # response.done now arrives -- the deferred create must carry the
    # instructions that were stashed above, and clear them afterward.
    loop._stop = threading.Event()
    loop._connection_lost = threading.Event()
    loop._connection_error = None
    loop._transcript = []
    loop._output_captioned = True
    loop._pending_calls = []
    with patch("argus.voice.realtime.ui_events.publish"):
        loop._receive([json.dumps({"type": "response.done"})])

    assert sent[-1] == {
        "type": "response.create",
        "response": {
            "instructions": 'Say exactly this to the user, word for word, then stop: '
                             '"May I delete email? Say yes or no."',
        },
    }
    assert loop._pending_create_instructions is None


def test_two_consecutive_confirmations_leave_no_residue(monkeypatch):
    """Unit 24a acceptance: two confirmations in one session must not
    accumulate -- the second attempt's response.create must carry only its
    own instructions, with nothing left over from the first (the original
    bug was exactly this kind of accumulation, just via conversation items
    instead of this field)."""
    loop = RealtimeVoiceLoop.__new__(RealtimeVoiceLoop)
    loop._socket = object()
    loop._response_active = False
    loop._pending_create_after_cancel = False
    loop._pending_create_instructions = None
    loop._playback_lock = threading.Lock()
    loop._playback = np.empty(0, dtype="int16")
    loop._output = queue.Queue()
    monkeypatch.setattr("argus.voice.realtime.settings.voice_confirm_speak_timeout_seconds", 0.0)
    sent = []
    loop._send = lambda socket, event: sent.append(event)

    with patch("argus.voice.realtime.ui_commands.get_confirmation_answer", return_value="yes"), \
         patch("argus.voice.realtime.ui_commands.set_voice_confirmation_active"), \
         patch("argus.voice.realtime.ui_events.publish"):
        loop._ask_voice_confirmation("First question, say yes or no.")
        loop._ask_voice_confirmation("Second question, say yes or no.")

    assert loop._pending_create_instructions is None
    assert not any(event["type"] == "conversation.item.create" for event in sent)
    assert sent[0]["response"]["instructions"].endswith('"First question, say yes or no."')
    assert sent[1]["response"]["instructions"].endswith('"Second question, say yes or no."')


def test_announce_and_submit_text_message_are_unaffected_by_the_24a_fix():
    """Unit 24a explicitly must leave announce() and submit_text_message()
    alone -- they legitimately add real conversation items (a proactive
    remark, a real user turn) and are not the bug."""
    loop = RealtimeVoiceLoop.__new__(RealtimeVoiceLoop)
    fake_socket = object()
    loop._socket = fake_socket
    loop._response_active = False
    loop._playback = np.empty(0, dtype="int16")
    loop._output = queue.Queue()
    loop._playback_lock = threading.Lock()
    loop._send_lock = threading.Lock()
    sent = []
    loop._send = lambda socket, event: sent.append(event)

    with patch("argus.voice.realtime.ui_events.publish"):
        assert loop.announce("you've got mail") is True
    assert sent[0]["type"] == "conversation.item.create"
    assert sent[0]["item"]["role"] == "system"

    sent.clear()
    with patch("argus.voice.realtime.ui_events.publish"):
        assert loop.submit_text_message("what's the weather") is True
    assert sent[0]["type"] == "conversation.item.create"
    assert sent[0]["item"]["role"] == "user"


def test_transcript_during_pending_confirmation_is_diverted_not_treated_as_a_new_turn():
    """This is the actual mechanism behind "verbal yes didn't work": the
    old confirmer had no path for the mic's normal transcript stream to
    resolve a pending confirmation at all. Confirmed here that a spoken
    answer arriving while a confirmation is active is routed to
    submit_confirmation_answer instead of falling into the ordinary
    should_use_tools/response.create turn-handling path."""
    import json
    import threading

    loop = RealtimeVoiceLoop.__new__(RealtimeVoiceLoop)
    loop._stop = threading.Event()
    loop._connection_lost = threading.Event()
    loop._connection_error = None
    loop._transcript = []
    loop._output_captioned = True
    loop._pending_calls = []
    loop._pending_create_after_cancel = False
    loop._speech_lock = threading.Lock()
    loop._resume_timer = None
    loop.tools = MagicMock()
    sent = []
    loop._send = lambda socket, event: sent.append(event)
    event = json.dumps({
        "type": "conversation.item.input_audio_transcription.completed",
        "transcript": "yes",
    })

    with patch("argus.voice.realtime.ui_commands.is_voice_confirmation_active", return_value=True), \
         patch("argus.voice.realtime.ui_commands.submit_confirmation_answer") as submit, \
         patch("argus.voice.realtime.ui_events.publish"):
        loop._receive([event])

    submit.assert_called_once_with("yes")
    # No response.create/cancel and no task-autonomy reset -- this must
    # not be treated as an ordinary new conversational turn.
    assert sent == []
    loop.tools.reset_task_autonomy.assert_not_called()


def test_acknowledgment_phrase_during_pending_confirmation_is_diverted_not_acknowledged():
    """PRD §15 unit 32: the existing is_voice_confirmation_active()
    diversion must still run FIRST and unchanged -- a yes/no (or here, an
    acknowledgment phrase that happens to also be a plausible answer)
    answering a permission prompt goes to the confirmer, never to
    acknowledgment. The acknowledgment check must never even be reached."""
    import json
    import threading

    loop = RealtimeVoiceLoop.__new__(RealtimeVoiceLoop)
    loop._stop = threading.Event()
    loop._connection_lost = threading.Event()
    loop._connection_error = None
    loop._transcript = []
    loop._output_captioned = True
    loop._pending_calls = []
    loop._pending_create_after_cancel = False
    loop._speech_lock = threading.Lock()
    loop._resume_timer = None
    loop.tools = MagicMock()
    loop.proactive = MagicMock()
    sent = []
    loop._send = lambda socket, event: sent.append(event)
    event = json.dumps({
        "type": "conversation.item.input_audio_transcription.completed",
        "transcript": "got it",
    })

    with patch("argus.voice.realtime.ui_commands.is_voice_confirmation_active", return_value=True), \
         patch("argus.voice.realtime.ui_commands.submit_confirmation_answer") as submit, \
         patch("argus.voice.realtime.ui_events.publish"):
        loop._receive([event])

    submit.assert_called_once_with("got it")
    loop.proactive.acknowledge_thread.assert_not_called()


def test_acknowledgment_phrase_on_a_non_diverted_transcript_acknowledges_the_spoken_thread():
    """Only reachable once the diversion above found nothing to divert."""
    import json
    import threading

    loop = RealtimeVoiceLoop.__new__(RealtimeVoiceLoop)
    loop._stop = threading.Event()
    loop._connection_lost = threading.Event()
    loop._connection_error = None
    loop._speech_lock = threading.Lock()
    loop._input_had_transcript = False
    loop._resume_timer = None
    loop._response_active = False
    loop._playback = np.empty(0, dtype="int16")
    loop._output = queue.Queue()
    loop._playback_lock = threading.Lock()
    loop._send_lock = threading.Lock()
    loop._last_expression = None
    loop.tools = MagicMock()
    loop.proactive = MagicMock()
    loop.proactive.dispatcher.last_spoken_thread_id = 42
    loop.proactive.dispatcher.last_spoken_ts = 1_000_000.0
    loop.proactive.acknowledge_thread.return_value = True
    event = json.dumps({
        "type": "conversation.item.input_audio_transcription.completed",
        "transcript": "got it",
    })

    with patch("argus.voice.realtime.ui_commands.is_voice_confirmation_active", return_value=False), \
         patch("argus.voice.realtime.ui_events.publish"), \
         patch("argus.voice.acknowledgment.time.time", return_value=1_000_010.0):
        loop._receive([event])

    loop.proactive.acknowledge_thread.assert_called_once_with(42, via="voice")


def test_bare_yes_on_a_non_diverted_transcript_never_acknowledges():
    import json
    import threading

    loop = RealtimeVoiceLoop.__new__(RealtimeVoiceLoop)
    loop._stop = threading.Event()
    loop._connection_lost = threading.Event()
    loop._connection_error = None
    loop._speech_lock = threading.Lock()
    loop._input_had_transcript = False
    loop._resume_timer = None
    loop._response_active = False
    loop._playback = np.empty(0, dtype="int16")
    loop._output = queue.Queue()
    loop._playback_lock = threading.Lock()
    loop._send_lock = threading.Lock()
    loop._last_expression = None
    loop.tools = MagicMock()
    loop.proactive = MagicMock()
    loop.proactive.dispatcher.last_spoken_thread_id = 42
    loop.proactive.dispatcher.last_spoken_ts = 1_000_000.0
    event = json.dumps({
        "type": "conversation.item.input_audio_transcription.completed",
        "transcript": "yes",
    })

    with patch("argus.voice.realtime.ui_commands.is_voice_confirmation_active", return_value=False), \
         patch("argus.voice.realtime.ui_events.publish"):
        loop._receive([event])

    loop.proactive.acknowledge_thread.assert_not_called()
