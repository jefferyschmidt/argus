import threading
from unittest.mock import MagicMock, patch

import numpy as np

from argus.voice.loop import VoiceLoop


def _loop_with_wake_sequence(sequence):
    loop = VoiceLoop.__new__(VoiceLoop)  # skip __init__ (loads real models)
    loop.orchestrator = MagicMock()
    loop._interaction_lock = threading.Lock()
    loop.wake_word = MagicMock()
    loop.wake_word.listen_for_wake_and_command.side_effect = sequence
    loop._refresh_hot_mic = MagicMock()
    loop._start_hearing_watcher = MagicMock(return_value=MagicMock())
    return loop


def test_wake_command_text_is_passed_through_without_retranscribing():
    """The whole point of the local engine: when it already transcribed the
    command in the same breath as the wake word, that text must be used
    directly -- re-transcribing samples would mean a redundant Groq call,
    defeating the "no ongoing API cost" design goal."""
    loop = _loop_with_wake_sequence([
        (np.array([1, 2, 3], dtype=np.int16), "what time is it"),
        KeyboardInterrupt(),
    ])
    loop._process_utterance = MagicMock(return_value=False)

    with patch("argus.voice.loop.ui_events.publish"):
        loop.run()

    loop._process_utterance.assert_called_once_with(text="what time is it", check_addressee=False)


def test_no_wake_command_text_falls_back_to_samples():
    """openWakeWord (and the local engine when only the wake word itself
    was said) never has pre-transcribed text -- must fall back to the
    normal samples-in, transcribe-as-usual path."""
    samples = np.array([4, 5, 6], dtype=np.int16)
    loop = _loop_with_wake_sequence([
        (samples, None),
        KeyboardInterrupt(),
    ])
    loop._process_utterance = MagicMock(return_value=False)

    with patch("argus.voice.loop.ui_events.publish"):
        loop.run()

    loop._process_utterance.assert_called_once_with(samples, check_addressee=False)


def test_local_engine_is_selected_by_default(monkeypatch):
    monkeypatch.setattr("argus.voice.loop.settings.wake_word_engine", "local")
    with patch("argus.voice.local_wake_word.LocalWakeWordListener.__init__", return_value=None) as mock_init:
        listener = VoiceLoop._build_wake_word_listener()

    mock_init.assert_called_once()
    from argus.voice.local_wake_word import LocalWakeWordListener
    assert isinstance(listener, LocalWakeWordListener)


def test_openwakeword_engine_is_selected_when_configured(monkeypatch):
    monkeypatch.setattr("argus.voice.loop.settings.wake_word_engine", "openwakeword")
    with patch("argus.voice.wake_word.WakeWordListener.__init__", return_value=None) as mock_init:
        listener = VoiceLoop._build_wake_word_listener()

    mock_init.assert_called_once()
    from argus.voice.wake_word import WakeWordListener
    assert isinstance(listener, WakeWordListener)


def test_run_passes_an_on_checking_callback_that_publishes_a_state_update():
    """Confirmed live as a real UX gap: local transcription to check for
    the wake word can take a genuinely noticeable few seconds, and until
    this the console gave zero feedback during that window -- reported
    live as "it heard me but isn't doing anything." """
    loop = _loop_with_wake_sequence([
        (np.array([1, 2, 3], dtype=np.int16), None),
        KeyboardInterrupt(),
    ])
    loop._process_utterance = MagicMock(return_value=False)

    with patch("argus.voice.loop.ui_events.publish") as mock_publish:
        loop.run()
        on_checking = loop.wake_word.listen_for_wake_and_command.call_args.kwargs["on_checking"]
        assert on_checking is not None
        on_checking()

    published_types_and_modes = [
        (call.args[0].get("type"), call.args[0].get("mode")) for call in mock_publish.call_args_list
    ]
    assert ("state", "confirming") in published_types_and_modes


def test_run_passes_a_rejection_callback_that_restores_wake_word_state():
    loop = _loop_with_wake_sequence([
        (np.array([1, 2, 3], dtype=np.int16), None),
        KeyboardInterrupt(),
    ])
    loop._process_utterance = MagicMock(return_value=False)

    with patch("argus.voice.loop.ui_events.publish") as mock_publish:
        loop.run()
        on_not_addressed = loop.wake_word.listen_for_wake_and_command.call_args.kwargs["on_not_addressed"]
        on_not_addressed()

    published = [call.args[0] for call in mock_publish.call_args_list]
    assert {"type": "state", "value": "listening", "mode": "wake_word"} in published
