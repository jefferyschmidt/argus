from unittest.mock import MagicMock, patch

import numpy as np

from argus.voice.wake_word import WakeWordListener


def test_listen_for_wake_and_command_returns_samples_and_none_text():
    """Return shape must stay (samples, text) even though this engine never
    has pre-transcribed text -- voice/loop.py unpacks both engines the same
    way (see LocalWakeWordListener, which sometimes has real text here)."""
    listener = WakeWordListener.__new__(WakeWordListener)
    listener._model = MagicMock()
    listener._model.predict.return_value = {"hey_jarvis_v0.1": 0.9}  # every frame "detects"

    frame = np.zeros(1280, dtype=np.int16)
    mock_stream = MagicMock()
    mock_stream.read.return_value = (frame, None)

    with patch("argus.voice.wake_word.sd.InputStream") as mock_stream_cls, \
         patch("argus.voice.wake_word.settings.wake_word_threshold", 0.5), \
         patch("argus.voice.wake_word.settings.voice_silence_rms_threshold", 999999):
        mock_stream_cls.return_value.__enter__.return_value = mock_stream
        result = listener.listen_for_wake_and_command()

    samples, text = result
    assert isinstance(samples, np.ndarray)
    assert text is None
