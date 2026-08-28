import threading
import time
from unittest.mock import MagicMock, patch

import numpy as np

from argus.voice.loop import VoiceLoop


def _loop_with_fast_playback():
    loop = VoiceLoop.__new__(VoiceLoop)  # skip __init__ (loads real models)
    loop.speaker = MagicMock()
    # Short fake audio, no visemes -- takes the envelope path.
    samples = np.zeros(1600, dtype=np.int16)  # 0.1s at 16kHz
    loop.speaker.synthesize_with_visemes.return_value = None
    loop.speaker.synthesize.return_value = (samples, 16000)
    loop._hot_mic_until = 0.0
    return loop


def test_hung_barge_in_watcher_does_not_block_the_turn(monkeypatch):
    """Confirmed live as the actual reported bug: _watch_for_barge_in used
    to run synchronously, so a stall in its own blocking mic read could
    hang the entire turn -- and the visible "speaking" state along with
    it -- forever, even with playback long since finished. This is the
    direct regression guard: even when the watcher never returns at all,
    _speak_with_barge_in must still return within the bounded grace
    period, not hang indefinitely."""
    monkeypatch.setattr("argus.voice.loop._BARGE_IN_WATCHER_GRACE_SECONDS", 0.2)
    loop = _loop_with_fast_playback()

    def _hangs_forever(stop_event, play_thread):
        threading.Event().wait()  # never returns
        return False

    loop._watch_for_barge_in = _hangs_forever

    with patch("argus.voice.audio_io.play_audio", return_value=None), \
         patch("argus.ui.events.publish"):
        started = time.monotonic()
        result = loop._speak_with_barge_in("hello")
        elapsed = time.monotonic() - started

    assert result is False
    assert elapsed < 3.0  # bounded by playback + the (shortened) grace period, not the hang


def test_normal_playback_with_no_interruption_returns_promptly(monkeypatch):
    monkeypatch.setattr("argus.voice.loop._BARGE_IN_WATCHER_GRACE_SECONDS", 0.5)
    loop = _loop_with_fast_playback()
    loop._watch_for_barge_in = MagicMock(return_value=False)

    with patch("argus.voice.audio_io.play_audio", return_value=None), \
         patch("argus.ui.events.publish"):
        result = loop._speak_with_barge_in("hello")

    assert result is False


def test_a_real_interruption_still_refreshes_hot_mic(monkeypatch):
    monkeypatch.setattr("argus.voice.loop._BARGE_IN_WATCHER_GRACE_SECONDS", 0.5)
    loop = _loop_with_fast_playback()
    loop._watch_for_barge_in = MagicMock(return_value=True)
    loop._refresh_hot_mic = MagicMock()

    with patch("argus.voice.audio_io.play_audio", return_value=None), \
         patch("argus.ui.events.publish"):
        result = loop._speak_with_barge_in("hello")

    assert result is True
    loop._refresh_hot_mic.assert_called_once()


def test_watcher_exception_does_not_propagate():
    loop = _loop_with_fast_playback()

    def _raises(stop_event, play_thread):
        raise RuntimeError("boom")

    loop._watch_for_barge_in = _raises

    with patch("argus.voice.audio_io.play_audio", return_value=None), \
         patch("argus.ui.events.publish"):
        result = loop._speak_with_barge_in("hello")  # must not raise

    assert result is False
