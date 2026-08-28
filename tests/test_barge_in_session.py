import threading
from unittest.mock import MagicMock, patch

from argus.voice.loop import VoiceLoop, _SpeechSession


def _loop():
    loop = VoiceLoop.__new__(VoiceLoop)
    loop._hot_mic_until = 0.0
    loop.speaker = MagicMock()
    loop.speaker.synthesize_with_visemes.return_value = None
    loop.speaker.synthesize.return_value = (b"\x00\x00", 16000)
    return loop


def test_session_watcher_covers_the_gap_between_sentences():
    """The whole point: the old per-sentence watcher only listened while
    audio was actually playing, so the 1.8s median gap between sentences --
    exactly where a person naturally interrupts -- had nothing listening.
    A detection during that gap must still count."""
    session = _SpeechSession()

    # Nothing is playing right now -- this is the between-sentences gap.
    session.on_detect()

    assert session.interrupted.is_set()


def test_a_gap_detection_stops_the_next_sentence_before_it_plays():
    loop = _loop()
    session = _SpeechSession()
    session.on_detect()          # barged in during the gap
    loop._speech_session = session

    with patch("argus.voice.loop.ui_commands.is_quiet_mode", return_value=False), \
         patch("argus.voice.loop.ui_events.publish"):
        interrupted = loop._speak_with_barge_in("The sentence that should never play.")

    assert interrupted is True
    # Not even synthesized -- no wasted TTS call for speech nobody hears.
    loop.speaker.synthesize_with_visemes.assert_not_called()
    loop.speaker.synthesize.assert_not_called()


def test_attach_playback_immediately_stops_audio_queued_after_a_gap_detection():
    """Guards the race where a sentence's playback starts in the instant
    between the detection and the session being told about it."""
    session = _SpeechSession()
    session.on_detect()

    play_stop = threading.Event()
    session.attach_playback(play_stop)

    assert play_stop.is_set()


def test_detection_while_playing_cuts_that_playback_off():
    session = _SpeechSession()
    play_stop = threading.Event()
    session.attach_playback(play_stop)

    session.on_detect()

    assert play_stop.is_set()
    assert session.interrupted.is_set()


def test_detaching_playback_does_not_leave_a_stale_stop_event():
    session = _SpeechSession()
    finished = threading.Event()
    session.attach_playback(finished)
    session.attach_playback(None)   # sentence done

    session.on_detect()             # fires during the following gap

    assert session.interrupted.is_set()
    assert not finished.is_set()    # the already-finished sentence isn't touched


def test_session_starts_exactly_one_watcher_for_a_whole_reply():
    loop = _loop()
    started = []

    def fake_watch(should_continue, on_detect):
        started.append(should_continue)
        return False

    with patch("argus.voice.loop.settings.voice_barge_in_enabled", True), \
         patch("argus.voice.loop.ui_commands.is_quiet_mode", return_value=False), \
         patch.object(loop, "_watch_for_barge_in", side_effect=fake_watch):
        with loop._barge_in_session():
            assert loop._speech_session is not None
        assert loop._speech_session is None   # always torn down

    assert len(started) == 1


def test_nested_sessions_do_not_start_a_second_watcher():
    loop = _loop()
    starts = []

    with patch("argus.voice.loop.settings.voice_barge_in_enabled", True), \
         patch("argus.voice.loop.ui_commands.is_quiet_mode", return_value=False), \
         patch.object(loop, "_watch_for_barge_in", side_effect=lambda **kw: starts.append(1) or False):
        with loop._barge_in_session():
            outer = loop._speech_session
            with loop._barge_in_session():
                assert loop._speech_session is outer   # reuses the open one

    assert len(starts) == 1


def test_no_session_in_quiet_mode():
    """Nothing is played aloud, so there's nothing to interrupt -- and a
    watcher listening anyway could flag a stray noise as a barge-in against
    silence."""
    loop = _loop()

    with patch("argus.voice.loop.settings.voice_barge_in_enabled", True), \
         patch("argus.voice.loop.ui_commands.is_quiet_mode", return_value=True), \
         patch.object(loop, "_watch_for_barge_in") as mock_watch:
        with loop._barge_in_session():
            assert loop._speech_session is None

    mock_watch.assert_not_called()


def test_no_session_when_barge_in_is_disabled():
    loop = _loop()

    with patch("argus.voice.loop.settings.voice_barge_in_enabled", False), \
         patch("argus.voice.loop.ui_commands.is_quiet_mode", return_value=False), \
         patch.object(loop, "_watch_for_barge_in") as mock_watch:
        with loop._barge_in_session():
            assert loop._speech_session is None

    mock_watch.assert_not_called()


def test_session_is_torn_down_even_if_the_body_raises():
    loop = _loop()

    with patch("argus.voice.loop.settings.voice_barge_in_enabled", True), \
         patch("argus.voice.loop.ui_commands.is_quiet_mode", return_value=False), \
         patch.object(loop, "_watch_for_barge_in", return_value=False):
        try:
            with loop._barge_in_session():
                raise RuntimeError("turn blew up")
        except RuntimeError:
            pass

    assert loop._speech_session is None
