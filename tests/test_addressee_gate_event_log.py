from unittest.mock import MagicMock, patch

import numpy as np

from argus.voice.loop import VoiceLoop


def _loop():
    loop = VoiceLoop.__new__(VoiceLoop)
    loop.transcriber = MagicMock()
    loop.speech_detector = MagicMock()
    loop.orchestrator = MagicMock()
    loop._speak_with_barge_in = MagicMock(return_value=False)
    loop._seems_addressed_to_argus = MagicMock(return_value=False)
    return loop


def test_a_dropped_utterance_publishes_an_addressee_gate_event():
    """Confirmed directly requested: nothing previously recorded WHICH
    utterances got silently dropped by the addressee gate or why -- a
    recurring complaint ("struggling to figure out when I'm talking to
    him") was previously only guessable from console scrollback that's
    long gone by the time it's reported."""
    loop = _loop()

    with patch("argus.voice.loop.ui_events.publish") as mock_publish:
        result = loop._process_utterance(text="that sounds expensive", check_addressee=True)

    assert result is True  # follow-up window stays open
    published = [call.args[0] for call in mock_publish.call_args_list]
    assert {
        "type": "addressee_gate", "verdict": "stray", "text": "that sounds expensive", "loudness": None,
    } in published


def test_addressed_utterance_does_not_publish_a_gate_event():
    loop = _loop()
    loop._seems_addressed_to_argus.return_value = True
    loop.orchestrator.handle_streaming.return_value = None

    with patch("argus.voice.loop.ui_events.publish") as mock_publish:
        loop._process_utterance(text="what time is it", check_addressee=True)

    published_types = [call.args[0].get("type") for call in mock_publish.call_args_list]
    assert "addressee_gate" not in published_types
