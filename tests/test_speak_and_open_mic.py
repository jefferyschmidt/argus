import threading
from unittest.mock import MagicMock, patch

import numpy as np

from argus.voice.loop import VoiceLoop


def _bare_loop():
    loop = VoiceLoop.__new__(VoiceLoop)
    loop._speak_with_barge_in = MagicMock(return_value=False)
    loop._hot_mic_until = 0.0
    return loop


def test_speak_and_open_mic_speaks_and_refreshes_the_hot_mic_window():
    loop = _bare_loop()

    with patch("argus.voice.loop.settings.open_barge_in_seconds", 30.0), \
         patch("argus.voice.loop.ui_commands.is_listening_paused", return_value=False):
        loop._speak_and_open_mic("Sounds like there's something on your mind. What's up?")

    loop._speak_with_barge_in.assert_called_once_with("Sounds like there's something on your mind. What's up?")
    assert loop._hot_mic_active() is True


def test_speak_and_open_mic_returns_whether_barge_in_interrupted():
    loop = _bare_loop()
    loop._speak_with_barge_in.return_value = True

    with patch("argus.voice.loop.settings.open_barge_in_seconds", 30.0):
        result = loop._speak_and_open_mic("hello")

    assert result is True


def test_run_passes_hot_mic_active_as_the_hot_mic_check_callback():
    """Confirmed live as a real gap: replying to something Argus said
    unprompted (a proactive check-in, an email alert) still needed the
    wake word, since only the normal reply path refreshed the hot-mic
    window the wake-word listener could see."""
    loop = VoiceLoop.__new__(VoiceLoop)
    loop.orchestrator = MagicMock()
    loop._interaction_lock = threading.Lock()
    loop.wake_word = MagicMock()
    loop.wake_word.listen_for_wake_and_command.side_effect = [
        (np.array([1, 2, 3], dtype=np.int16), None),
        KeyboardInterrupt(),
    ]
    loop._refresh_hot_mic = MagicMock()
    loop._start_hearing_watcher = MagicMock(return_value=MagicMock())
    loop._process_utterance = MagicMock(return_value=False)

    with patch("argus.voice.loop.ui_events.publish"):
        loop.run()

    call_kwargs = loop.wake_word.listen_for_wake_and_command.call_args.kwargs
    assert call_kwargs["hot_mic_check"] == loop._hot_mic_active
