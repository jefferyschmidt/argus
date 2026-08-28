from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from argus.voice.audio_io import ListeningPaused, record_followup
from argus.voice.local_wake_word import LocalWakeWordListener


def test_record_followup_raises_listening_paused_mid_capture():
    """Confirmed live as a real gap: "Stop listening" only took effect
    between listen attempts, so the mic kept actively capturing for the
    rest of whatever was already in progress -- reported live as needing
    to "basically be a mute input button." should_stop must be checked
    every frame, not just once per call."""
    mock_stream = MagicMock()
    mock_stream.read.return_value = (np.zeros(1000, dtype=np.int16), None)
    should_stop = MagicMock(side_effect=[False, False, True])

    with patch("argus.voice.audio_io.sd.InputStream") as mock_stream_cls, \
         patch("argus.voice.audio_io.settings.voice_silence_rms_threshold", 999999):
        mock_stream_cls.return_value.__enter__.return_value = mock_stream
        with pytest.raises(ListeningPaused):
            record_followup(30.0, should_stop=should_stop)

    # Stopped well before the full frame budget -- proves it didn't keep
    # reading after should_stop fired.
    assert mock_stream.read.call_count == 2


def _listener():
    listener = LocalWakeWordListener.__new__(LocalWakeWordListener)
    listener._vad = MagicMock()
    listener._transcriber = MagicMock()
    return listener


def test_local_wake_word_raises_listening_paused_between_utterances():
    listener = _listener()
    should_stop = MagicMock(side_effect=[False, True])

    with patch.object(listener, "_capture_one_utterance", return_value=None), \
         patch("argus.voice.local_wake_word.sd.InputStream") as mock_stream_cls:
        mock_stream_cls.return_value.__enter__.return_value = MagicMock()
        with pytest.raises(ListeningPaused):
            listener.listen_for_wake_and_command(should_stop=should_stop)


def test_local_wake_word_raises_listening_paused_mid_frame_capture():
    """The deeper fix: should_stop is checked inside _capture_one_utterance
    itself, every frame -- not just between utterances -- since that's
    where most of the actual blocking time happens."""
    listener = _listener()
    mock_stream = MagicMock()
    mock_stream.read.return_value = (np.zeros(512, dtype=np.int16), None)
    listener._vad.is_speech.return_value = False
    should_stop = MagicMock(side_effect=[False, False, True])

    with patch("argus.voice.local_wake_word.sd.InputStream") as mock_stream_cls:
        mock_stream_cls.return_value.__enter__.return_value = mock_stream
        with pytest.raises(ListeningPaused):
            listener.listen_for_wake_and_command(should_stop=should_stop)

    # The outer loop's own should_stop check consumes the first call; the
    # remaining two are inside _capture_one_utterance's per-frame check.
    assert mock_stream.read.call_count == 1
