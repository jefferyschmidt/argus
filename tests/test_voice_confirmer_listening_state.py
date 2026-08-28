from unittest.mock import MagicMock, patch


def test_try_voice_publishes_listening_state_while_waiting_for_a_spoken_answer():
    """Confirmed live as a real gap: nothing published a "listening" state
    while _try_voice waited on the mic, so the console never showed Argus
    was actually listening for the yes/no -- reported as "didn't go to
    'listening' to hear my response." """
    from argus.voice.confirm import make_voice_confirmer

    speaker = MagicMock()
    transcriber = MagicMock()
    transcriber.transcribe.return_value = "yes"
    confirmer = make_voice_confirmer(speaker, transcriber)

    with patch("argus.voice.audio_io.record_followup", return_value=[1, 2, 3]), \
         patch("argus.ui.events.publish") as mock_publish:
        result = confirmer("click", {"x": 10, "y": 10})

    assert result is True
    published = [call.args[0] for call in mock_publish.call_args_list]
    assert {"type": "state", "value": "listening", "mode": "confirming"} in published
    assert any(e.get("type") == "state" and e.get("value") == "thinking" for e in published)
