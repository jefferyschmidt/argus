from unittest.mock import MagicMock, patch

from argus.voice.loop import VoiceLoop


def _loop():
    loop = VoiceLoop.__new__(VoiceLoop)
    loop._interaction_lock = MagicMock()
    loop._process_utterance = MagicMock()
    loop._start_hearing_watcher = MagicMock(return_value=MagicMock())
    return loop


def _run_one_ptt_cycle(loop, get_text_message_returns_none_then_raises=True):
    def fake_get_text_message(timeout=None):
        return None

    ptt_calls = {"n": 0}

    def fake_is_ptt_active():
        ptt_calls["n"] += 1
        # Active for exactly one _external_input_worker iteration, then
        # stop the loop the same way a KeyboardInterrupt from the test
        # harness would.
        if ptt_calls["n"] == 1:
            return True
        raise KeyboardInterrupt()

    with patch("argus.voice.loop.ui_commands.get_text_message", side_effect=fake_get_text_message), \
         patch("argus.voice.loop.ui_commands.is_push_to_talk_active", side_effect=fake_is_ptt_active), \
         patch("argus.voice.audio_io.record_while", return_value="samples") as mock_record:
        try:
            loop._external_input_worker()
        except KeyboardInterrupt:
            pass
    return mock_record


def test_ptt_interrupts_an_in_progress_speech_session_immediately():
    """Confirmed live: "I can't interrupt him with the hold to talk
    button" -- pressing PTT while Argus is speaking must signal
    interruption right away, the same mechanism voice barge-in uses, not
    silently wait for the current turn to finish on its own."""
    loop = _loop()
    session = MagicMock()
    loop._speech_session = session

    _run_one_ptt_cycle(loop)

    session.on_detect.assert_called_once()


def test_ptt_recording_happens_before_acquiring_the_interaction_lock():
    """Confirmed live as a real bug ("he cut me off before I finished
    talking"): recording used to happen INSIDE the interaction lock, so if
    Argus was busy, capture didn't start until the lock freed up -- by
    which point the button may already have been released, missing the
    utterance entirely. record_while must be called before the lock is
    acquired, so capture starts the instant the button is pressed."""
    loop = _loop()
    loop._speech_session = None

    call_order = []
    loop._interaction_lock.__enter__ = MagicMock(side_effect=lambda: call_order.append("lock_acquired"))
    loop._interaction_lock.__exit__ = MagicMock(return_value=False)

    with patch("argus.voice.loop.ui_commands.get_text_message", return_value=None), \
         patch("argus.voice.loop.ui_commands.is_push_to_talk_active", side_effect=[True, KeyboardInterrupt()]), \
         patch("argus.voice.audio_io.record_while", side_effect=lambda *a, **k: call_order.append("recorded") or "samples"):
        try:
            loop._external_input_worker()
        except KeyboardInterrupt:
            pass

    assert call_order == ["recorded", "lock_acquired"]
    loop._process_utterance.assert_called_once_with("samples")


def test_ptt_does_not_interrupt_when_no_speech_session_is_active():
    """No active speech session (Argus idle or mid-tool-call, not
    speaking) -- nothing to interrupt, must not raise."""
    loop = _loop()
    loop._speech_session = None

    _run_one_ptt_cycle(loop)  # must not raise

    loop._process_utterance.assert_called_once_with("samples")
