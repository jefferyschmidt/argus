from unittest.mock import patch

from argus.ui import commands as ui_commands


def test_pausing_plays_the_stopped_chime():
    with patch("argus.voice.chime.play_stopped_chime") as mock_stopped, \
         patch("argus.voice.chime.play_listening_chime") as mock_listening:
        ui_commands.set_listening_paused(True)

    mock_stopped.assert_called_once()
    mock_listening.assert_not_called()


def test_resuming_plays_the_listening_chime():
    with patch("argus.voice.chime.play_stopped_chime") as mock_stopped, \
         patch("argus.voice.chime.play_listening_chime") as mock_listening:
        ui_commands.set_listening_paused(False)

    mock_listening.assert_called_once()
    mock_stopped.assert_not_called()


def test_a_chime_failure_does_not_break_the_pause_toggle():
    """Best-effort: a missing/busy audio device must never break the
    actual pause/resume it's just signaling."""
    with patch("argus.voice.chime.play_stopped_chime", side_effect=OSError("no audio device")):
        ui_commands.set_listening_paused(True)  # must not raise

    assert ui_commands.is_listening_paused() is True
    ui_commands.set_listening_paused(False)
