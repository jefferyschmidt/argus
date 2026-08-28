import time
from unittest.mock import patch

from argus.voice.chime import play_listening_chime, play_stopped_chime


def test_play_listening_chime_plays_audio_without_blocking():
    with patch("argus.voice.chime.sd.play") as mock_play, patch("argus.voice.chime.sd.wait"):
        play_listening_chime()
        time.sleep(0.05)  # let the background thread run

    mock_play.assert_called_once()


def test_play_stopped_chime_plays_audio_without_blocking():
    with patch("argus.voice.chime.sd.play") as mock_play, patch("argus.voice.chime.sd.wait"):
        play_stopped_chime()
        time.sleep(0.05)

    mock_play.assert_called_once()


def test_listening_and_stopped_chimes_use_different_tones():
    from argus.voice.chime import _LISTENING_CHIME, _STOPPED_CHIME
    assert not (_LISTENING_CHIME == _STOPPED_CHIME).all()


def test_chime_playback_failure_does_not_raise():
    """Best-effort: a missing/busy audio device must never break the
    actual listening-state transition it's just signaling."""
    with patch("argus.voice.chime.sd.play", side_effect=OSError("no output device")):
        play_listening_chime()  # must not raise, even though the worker thread will log an exception
        time.sleep(0.05)
