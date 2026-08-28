from unittest.mock import MagicMock, patch

import numpy as np

from argus.voice.loop import VoiceLoop


def _loop():
    loop = VoiceLoop.__new__(VoiceLoop)  # bypass __init__ -- no mic/models needed
    loop.transcriber = MagicMock()
    loop.speech_detector = MagicMock()
    loop.orchestrator = MagicMock()
    loop.orchestrator.handle_streaming.return_value = None
    loop.orchestrator.last_tier = None
    loop._speak_with_barge_in = MagicMock(return_value=False)
    return loop


def _samples():
    return np.ones(16000, dtype=np.int16)


def test_non_speech_audio_is_never_sent_to_the_transcriber():
    """Confirmed live as a real, self-sustaining bug: Whisper hallucinates
    short boilerplate phrases ("thank you", "bye") when fed near-silent or
    ambient audio -- Argus's own TTS bleeding faintly back into the mic
    right after he finishes talking was enough to trigger it, and the
    hallucinated "thank you" then got treated as addressed, replied to,
    and its own echo restarted the cycle -- observed running for over a
    dozen exchanges unattended. A stricter check (Silero VAD on the actual
    audio, not just an RMS threshold) must reject it before Whisper is
    ever asked to transcribe it at all."""
    loop = _loop()
    loop.speech_detector.is_speech.return_value = False

    with patch("argus.voice.loop.ui_events.publish") as mock_publish:
        result = loop._process_utterance(samples=_samples())

    assert result is False
    loop.transcriber.transcribe.assert_not_called()
    # Confirmed live as a real, separate silent-drop path from the
    # addressee gate -- reported as "still ignoring my input sometimes,
    # without saying that he's disregarding me." This one never reaches
    # transcription at all, so it needs its own visibility event.
    mock_publish.assert_called_once_with({"type": "addressee_gate", "verdict": "not_speech", "text": None})


def test_real_speech_audio_still_gets_transcribed_normally():
    loop = _loop()
    loop.speech_detector.is_speech.return_value = True
    loop.transcriber.transcribe.return_value = "what time is it"

    loop._process_utterance(samples=_samples())

    loop.transcriber.transcribe.assert_called_once()


def test_pre_transcribed_text_skips_the_vad_check_entirely():
    """The local wake-word engine sometimes hands over already-transcribed
    command text (see LocalWakeWordListener) -- there's no raw audio
    decision to make in that case, and no reason to run VAD at all."""
    loop = _loop()

    loop._process_utterance(text="what time is it")

    loop.speech_detector.is_speech.assert_not_called()


def test_empty_samples_do_not_call_is_speech():
    loop = _loop()
    loop.transcriber.transcribe.return_value = ""  # matches Transcriber's own real behavior on empty input

    result = loop._process_utterance(samples=np.array([], dtype=np.int16))

    assert result is False
    loop.speech_detector.is_speech.assert_not_called()
