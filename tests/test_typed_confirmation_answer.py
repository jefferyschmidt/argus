from unittest.mock import MagicMock, patch

from argus.ui import commands as ui_commands
from argus.voice.confirm import make_voice_confirmer


def test_typed_yes_during_a_pending_voice_confirmation_is_used_as_the_answer():
    """Confirmed live as a real bug: typed text sent while a voice
    confirmation was pending got silently queued behind the interaction
    lock (already held by this exact call stack) and was never actually
    used as the answer -- looked exactly like "he's not processing it"
    because he genuinely wasn't. should_stop must be checked and cut the
    recording short the instant a typed answer arrives."""
    speaker = MagicMock()
    transcriber = MagicMock()
    confirmer = make_voice_confirmer(speaker, transcriber)

    def fake_record_followup(timeout, should_stop=None, **kwargs):
        ui_commands.submit_confirmation_answer("yes")
        assert should_stop() is True  # the caller must notice the typed answer
        return None

    with patch("argus.voice.audio_io.record_followup", side_effect=fake_record_followup):
        result = confirmer("run_shell", {"command": "dir"})

    assert result is True
    transcriber.transcribe.assert_not_called()  # answered by text, never needed to transcribe audio


def test_voice_confirmation_active_flag_is_set_during_and_cleared_after():
    speaker = MagicMock()
    transcriber = MagicMock()
    confirmer = make_voice_confirmer(speaker, transcriber)

    seen_active = {"during": None}

    def fake_record_followup(timeout, should_stop=None, **kwargs):
        seen_active["during"] = ui_commands.is_voice_confirmation_active()
        return None

    with patch("argus.voice.audio_io.record_followup", side_effect=fake_record_followup), \
         patch("argus.ui.commands.request_confirmation", return_value=1), \
         patch("argus.ui.commands.wait_for_confirmation", return_value=False):
        confirmer("run_shell", {"command": "dir"})

    assert seen_active["during"] is True
    assert ui_commands.is_voice_confirmation_active() is False


def test_stale_confirmation_answer_does_not_leak_into_the_next_window():
    ui_commands.set_voice_confirmation_active(True)
    ui_commands.submit_confirmation_answer("leftover from a previous prompt")
    ui_commands.set_voice_confirmation_active(False)

    assert ui_commands.get_confirmation_answer(timeout=0) is None


def test_external_input_worker_routes_text_to_confirmation_channel_when_active():
    """The other half of the fix: _external_input_worker must not route
    typed text into the normal utterance path (which would queue behind
    the lock the confirmation is holding) while a confirmation is
    pending."""
    from argus.voice.loop import VoiceLoop

    loop = VoiceLoop.__new__(VoiceLoop)
    loop._interaction_lock = MagicMock()
    loop._process_utterance = MagicMock()

    call_count = {"n": 0}

    def fake_get_text_message(timeout=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return "yes"
        raise KeyboardInterrupt()

    with patch("argus.voice.loop.ui_commands.get_text_message", side_effect=fake_get_text_message), \
         patch("argus.voice.loop.ui_commands.is_voice_confirmation_active", return_value=True), \
         patch("argus.voice.loop.ui_commands.submit_confirmation_answer") as mock_submit:
        try:
            loop._external_input_worker()
        except KeyboardInterrupt:
            pass

    mock_submit.assert_called_once_with("yes")
    loop._process_utterance.assert_not_called()
