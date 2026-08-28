from unittest.mock import MagicMock, patch

import numpy as np

from argus.llm.base import CompletionResult, Tier
from argus.voice.loop import VoiceLoop


def _samples_at_amplitude(amplitude: int, n: int = 1000) -> np.ndarray:
    return np.full(n, amplitude, dtype=np.int16)


def test_loudness_hint_is_none_without_samples():
    assert VoiceLoop._loudness_hint(None) is None
    assert VoiceLoop._loudness_hint(np.array([], dtype=np.int16)) is None


def test_quiet_audio_is_flagged_as_more_likely_background():
    """Confirmed directly requested: quieter sound is genuinely more likely
    to be background noise/conversation than a direct request -- previously
    the addressee gate looked only at the transcribed TEXT, never how
    loud/close the utterance actually was."""
    with patch("argus.voice.loop.settings.voice_silence_rms_threshold", 100.0):
        hint = VoiceLoop._loudness_hint(_samples_at_amplitude(150))  # ratio 1.5
    assert "quiet" in hint
    assert "distant" in hint or "incidental" in hint


def test_loud_close_audio_is_flagged_as_direct():
    with patch("argus.voice.loop.settings.voice_silence_rms_threshold", 100.0):
        hint = VoiceLoop._loudness_hint(_samples_at_amplitude(500))  # ratio 5.0
    assert "clear" in hint
    assert "direct" in hint


def _loop_with_local_reply(reply_text: str) -> VoiceLoop:
    loop = VoiceLoop.__new__(VoiceLoop)
    orchestrator = MagicMock()
    orchestrator.router.local.complete.return_value = CompletionResult(
        text=reply_text, tier=Tier.LOCAL, model="fake", input_tokens=1, output_tokens=1,
    )
    loop.orchestrator = orchestrator
    return loop


def test_quiet_ambiguous_utterance_is_flagged_stray_by_the_model_prompt():
    """Deliberately NOT gated on loudness in code -- the model still makes
    the call, just with the extra signal folded into its prompt alongside
    the words themselves."""
    loop = _loop_with_local_reply("STRAY")
    with patch("argus.voice.loop.settings.voice_silence_rms_threshold", 100.0):
        result = loop._seems_addressed_to_argus("yeah he said that too", samples=_samples_at_amplitude(120))

    assert result is False
    prompt = loop.orchestrator.router.local.complete.call_args[0][0][0].content
    assert "quiet" in prompt.lower()


def test_a_clear_direct_question_still_passes_regardless_of_volume():
    """A clean "?" or direct opener is a fast path that never even reaches
    the model -- must not be gated on loudness, since a real question asked
    quietly on purpose should still get through."""
    loop = _loop_with_local_reply("STRAY")  # would fail if this were even consulted
    with patch("argus.voice.loop.settings.voice_silence_rms_threshold", 100.0):
        result = loop._seems_addressed_to_argus("can you still hear me?", samples=_samples_at_amplitude(101))

    assert result is True
    loop.orchestrator.router.local.complete.assert_not_called()


def test_no_samples_means_no_loudness_line_in_the_prompt():
    loop = _loop_with_local_reply("ADDRESSED")
    loop._seems_addressed_to_argus("yeah he said that too", samples=None)

    prompt = loop.orchestrator.router.local.complete.call_args[0][0][0].content
    assert "Volume:" not in prompt
