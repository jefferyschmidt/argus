"""PRD.md §15 unit 32, voice/loop.py's side of it. Unlike
voice/realtime.py's _receive, no explicit is_voice_confirmation_active()
check is needed here: every call into _process_utterance already happens
under self._interaction_lock, and voice/confirm.py's confirmer captures
and consumes its own recording synchronously from within a tool call
that's already holding that same lock -- see the call site in loop.py
for the full reasoning. These tests exercise the acknowledgment wiring
itself, not that structural guarantee (which has no code to test)."""

from unittest.mock import MagicMock

from argus.ui import commands as ui_commands
from argus.voice.loop import VoiceLoop


def _loop(proactive=None):
    loop = VoiceLoop.__new__(VoiceLoop)  # bypass __init__ -- no mic/models needed
    loop.orchestrator = MagicMock()
    loop.orchestrator.last_tier = None
    loop.transcriber = MagicMock()
    loop._speak_with_barge_in = MagicMock(return_value=False)
    loop.proactive = proactive
    return loop


def test_acknowledgment_phrase_acknowledges_the_spoken_thread(monkeypatch):
    ui_commands.set_quiet_mode(False)
    proactive = MagicMock()
    proactive.dispatcher.last_spoken_thread_id = 42
    proactive.dispatcher.last_spoken_ts = 1_000_000.0
    proactive.acknowledge_thread.return_value = True
    loop = _loop(proactive)
    monkeypatch.setattr("argus.voice.acknowledgment.time.time", lambda: 1_000_010.0)

    loop._process_utterance(text="got it")

    proactive.acknowledge_thread.assert_called_once_with(42, via="voice")


def test_bare_yes_never_acknowledges():
    ui_commands.set_quiet_mode(False)
    proactive = MagicMock()
    proactive.dispatcher.last_spoken_thread_id = 42
    proactive.dispatcher.last_spoken_ts = 1_000_000.0
    loop = _loop(proactive)

    loop._process_utterance(text="yes")

    proactive.acknowledge_thread.assert_not_called()


def test_acknowledgment_phrase_with_no_recently_spoken_thread_does_nothing_and_still_processes_normally():
    ui_commands.set_quiet_mode(False)
    proactive = MagicMock()
    proactive.dispatcher.last_spoken_thread_id = None
    proactive.dispatcher.last_spoken_ts = None
    loop = _loop(proactive)

    result = loop._process_utterance(text="thanks")

    proactive.acknowledge_thread.assert_not_called()
    # Still a normal turn -- not silently swallowed.
    loop.orchestrator.handle_streaming.assert_called_once()
    assert result is True


def test_acknowledgment_check_does_not_suppress_the_rest_of_the_turn(monkeypatch):
    """A compound utterance like "got it, also look at the camera" must
    still reach the model as a real request -- acknowledging is a side
    effect, never a replacement for normal turn handling."""
    ui_commands.set_quiet_mode(False)
    proactive = MagicMock()
    proactive.dispatcher.last_spoken_thread_id = 42
    proactive.dispatcher.last_spoken_ts = 1_000_000.0
    proactive.acknowledge_thread.return_value = True
    loop = _loop(proactive)
    monkeypatch.setattr("argus.voice.acknowledgment.time.time", lambda: 1_000_010.0)

    loop._process_utterance(text="got it, also look at the camera")

    proactive.acknowledge_thread.assert_called_once()
    loop.orchestrator.handle_streaming.assert_called_once()


def test_no_proactive_engine_is_a_safe_no_op():
    """A partially-constructed loop (or, structurally, argus chat, which
    has no ProactiveEngine at all) must not crash on an acknowledgment
    phrase."""
    ui_commands.set_quiet_mode(False)
    loop = _loop(proactive=None)

    result = loop._process_utterance(text="got it")  # must not raise

    assert result is True
