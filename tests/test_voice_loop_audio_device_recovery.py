import threading
from unittest.mock import MagicMock, patch

import numpy as np
import sounddevice as sd

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


def test_port_audio_error_during_wake_word_listen_is_recovered_not_crashed():
    """Confirmed live as a real crash: a transient audio-device hiccup
    (Bluetooth mic dropout, another app grabbing exclusive access) raised
    all the way out of the wake-word listen call and killed the whole
    process instead of just that one attempt."""
    loop = _loop_with_wake_sequence([
        sd.PortAudioError("no driver installed"),
        KeyboardInterrupt(),
    ])
    loop._process_utterance = MagicMock(return_value=False)

    with patch("argus.voice.loop.ui_events.publish"), patch("argus.voice.loop.time.sleep") as mock_sleep:
        loop.run()  # must not raise

    mock_sleep.assert_called_once_with(3.0)
    assert loop.wake_word.listen_for_wake_and_command.call_count == 2
    loop._process_utterance.assert_not_called()  # never reached after the crash


def test_port_audio_error_during_follow_up_returns_to_wake_word_listening():
    loop = _loop_with_wake_sequence([
        (np.array([1, 2, 3], dtype=np.int16), None),
        KeyboardInterrupt(),
    ])
    loop._process_utterance = MagicMock(return_value=True)  # keep the follow-up loop open

    with patch("argus.voice.loop.ui_events.publish"), \
         patch("argus.voice.loop.time.sleep") as mock_sleep, \
         patch("argus.voice.loop.record_followup", side_effect=sd.PortAudioError("device unavailable")):
        loop.run()  # must not raise

    mock_sleep.assert_called_once_with(3.0)
    assert loop.wake_word.listen_for_wake_and_command.call_count == 2
