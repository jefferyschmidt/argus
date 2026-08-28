import threading
from unittest.mock import MagicMock, patch

import numpy as np

from argus.voice.loop import VoiceLoop


def _loop_with_wake_sequence(sequence):
    loop = VoiceLoop.__new__(VoiceLoop)
    loop.orchestrator = MagicMock()
    loop._interaction_lock = threading.Lock()
    loop.wake_word = MagicMock()
    loop.wake_word.listen_for_wake_and_command.side_effect = sequence
    loop._refresh_hot_mic = MagicMock()
    loop._start_hearing_watcher = MagicMock(return_value=MagicMock())
    return loop


def test_genuine_wake_word_match_skips_the_addressee_gate():
    """A real "Argus, ..." match is always explicit intent -- unaffected
    by the hot-mic addressee fix."""
    calls = {"n": 0}

    def fake_listen(**kwargs):
        calls["n"] += 1
        if calls["n"] > 1:
            raise KeyboardInterrupt()
        return np.array([1, 2, 3], dtype=np.int16), "what time is it"

    loop = _loop_with_wake_sequence(None)
    loop.wake_word.listen_for_wake_and_command.side_effect = fake_listen
    loop._process_utterance = MagicMock(return_value=False)

    with patch("argus.voice.loop.ui_events.publish"):
        loop.run()

    loop._process_utterance.assert_called_once_with(text="what time is it", check_addressee=False)


def test_hot_mic_bypass_capture_still_goes_through_the_addressee_gate():
    """Confirmed live as a real incident: background video audio during an
    open hot-mic window (from a proactive nudge) got treated as a direct
    question and replied to, then a loud line from the SAME video
    triggered a false barge-in mid-reply, and once that was correctly
    judged not a real interruption, most of the reply got re-spoken --
    read live as "he repeated the whole thing." Root cause: the hot-mic
    bypass path was treated exactly like a real wake-word match, skipping
    the addressee gate entirely."""
    calls = {"n": 0}

    def fake_listen(*, via_hot_mic_out=None, **kwargs):
        calls["n"] += 1
        if calls["n"] > 1:
            raise KeyboardInterrupt()
        if via_hot_mic_out is not None:
            via_hot_mic_out.append(True)
        return np.array([1, 2, 3], dtype=np.int16), "some background video audio"

    loop = _loop_with_wake_sequence(None)
    loop.wake_word.listen_for_wake_and_command.side_effect = fake_listen
    loop._process_utterance = MagicMock(return_value=False)

    with patch("argus.voice.loop.ui_events.publish"):
        loop.run()

    loop._process_utterance.assert_called_once_with(text="some background video audio", check_addressee=True)


def test_hot_mic_bypass_with_raw_samples_also_gates():
    calls = {"n": 0}

    def fake_listen(*, via_hot_mic_out=None, **kwargs):
        calls["n"] += 1
        if calls["n"] > 1:
            raise KeyboardInterrupt()
        if via_hot_mic_out is not None:
            via_hot_mic_out.append(True)
        return np.array([4, 5, 6], dtype=np.int16), None

    loop = _loop_with_wake_sequence(None)
    loop.wake_word.listen_for_wake_and_command.side_effect = fake_listen
    loop._process_utterance = MagicMock(return_value=False)

    with patch("argus.voice.loop.ui_events.publish"):
        loop.run()

    samples_arg = loop._process_utterance.call_args[0][0]
    assert list(samples_arg) == [4, 5, 6]
    assert loop._process_utterance.call_args.kwargs["check_addressee"] is True
