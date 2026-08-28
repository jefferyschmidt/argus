from unittest.mock import MagicMock, patch

import numpy as np

from argus.voice.local_wake_word import LocalWakeWordListener


def _listener():
    listener = LocalWakeWordListener.__new__(LocalWakeWordListener)
    listener._vad = MagicMock()
    listener._transcriber = MagicMock()
    return listener


def test_hot_mic_active_lets_a_reply_through_with_no_wake_word():
    """Confirmed live as a real gap: anything Argus said on its own
    initiative (a proactive check-in, an email alert) never opened a
    hands-free follow-up window, so answering it directly with no wake
    word looked like Argus silently ignoring what was said."""
    listener = _listener()
    listener._transcriber.transcribe_local.return_value = "that sounds pretty expensive actually"
    hot_mic_check = MagicMock(return_value=True)
    captured = np.ones(16000, dtype=np.int16)

    with patch.object(listener, "_capture_one_utterance", return_value=captured), \
         patch("argus.voice.local_wake_word.sd.InputStream") as mock_stream_cls:
        mock_stream_cls.return_value.__enter__.return_value = MagicMock()
        samples, command = listener.listen_for_wake_and_command(hot_mic_check=hot_mic_check)

    # Accepted, and handed back as raw audio with no command text: inside a
    # hot-mic window the wake word is irrelevant, so the local transcript
    # has no consumer. The caller transcribes once, hosted, and only if the
    # utterance clears the addressee gate.
    assert samples is captured
    assert command is None


def test_hot_mic_skips_the_slow_local_transcription_entirely():
    """The single slowest step in the listening path (~3.2s warm, ~8.2s on
    the first call while the model loads). Inside an open hot-mic window
    nothing reads its output, so paying for it delayed every hands-free
    follow-up -- and every piece of background chatter -- for nothing."""
    listener = _listener()
    hot_mic_check = MagicMock(return_value=True)

    with patch.object(listener, "_capture_one_utterance", return_value=np.ones(16000, dtype=np.int16)), \
         patch("argus.voice.local_wake_word.sd.InputStream") as mock_stream_cls:
        mock_stream_cls.return_value.__enter__.return_value = MagicMock()
        listener.listen_for_wake_and_command(hot_mic_check=hot_mic_check)

    listener._transcriber.transcribe_local.assert_not_called()


def test_on_checking_is_not_fired_when_hot_mic_short_circuits():
    """on_checking exists to explain the local-transcription pause to the
    user. There is no such pause on this path, so announcing one would be
    a lie the console then has to sit on."""
    listener = _listener()
    on_checking = MagicMock()

    with patch.object(listener, "_capture_one_utterance", return_value=np.ones(16000, dtype=np.int16)), \
         patch("argus.voice.local_wake_word.sd.InputStream") as mock_stream_cls:
        mock_stream_cls.return_value.__enter__.return_value = MagicMock()
        listener.listen_for_wake_and_command(
            hot_mic_check=MagicMock(return_value=True), on_checking=on_checking
        )

    on_checking.assert_not_called()


def test_hot_mic_inactive_still_requires_the_wake_word():
    listener = _listener()
    listener._transcriber.transcribe_local.side_effect = ["no wake word here", "argus what time is it"]
    hot_mic_check = MagicMock(return_value=False)

    with patch.object(listener, "_capture_one_utterance", side_effect=[
        np.ones(16000, dtype=np.int16),
        np.ones(16000, dtype=np.int16),
    ]), patch("argus.voice.local_wake_word.sd.InputStream") as mock_stream_cls:
        mock_stream_cls.return_value.__enter__.return_value = MagicMock()
        samples, command = listener.listen_for_wake_and_command(hot_mic_check=hot_mic_check)

    assert command == "what time is it"
    assert hot_mic_check.call_count == 2


def test_hot_mic_check_fires_on_wake_callback_too():
    listener = _listener()
    listener._transcriber.transcribe_local.return_value = "sure, go ahead"
    on_wake = MagicMock()

    with patch.object(listener, "_capture_one_utterance", return_value=np.ones(16000, dtype=np.int16)), \
         patch("argus.voice.local_wake_word.sd.InputStream") as mock_stream_cls:
        mock_stream_cls.return_value.__enter__.return_value = MagicMock()
        listener.listen_for_wake_and_command(on_wake=on_wake, hot_mic_check=lambda: True)

    on_wake.assert_called_once()


def test_no_hot_mic_check_falls_back_to_normal_wake_word_matching():
    listener = _listener()
    listener._transcriber.transcribe_local.return_value = "no wake word said at all"

    with patch.object(listener, "_capture_one_utterance", side_effect=[
        np.ones(16000, dtype=np.int16),
        KeyboardInterrupt(),  # break out of the infinite loop after one miss
    ]), patch("argus.voice.local_wake_word.sd.InputStream") as mock_stream_cls:
        mock_stream_cls.return_value.__enter__.return_value = MagicMock()
        try:
            listener.listen_for_wake_and_command()
            assert False, "expected the loop to keep going past a non-matching utterance"
        except KeyboardInterrupt:
            pass
