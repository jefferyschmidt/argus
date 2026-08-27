from unittest.mock import MagicMock

from argus.llm.base import CompletionResult, Tier
from argus.voice.loop import VoiceLoop


def _make_loop_with_local_reply(reply_text: str) -> VoiceLoop:
    loop = VoiceLoop.__new__(VoiceLoop)  # skip __init__ (loads real models)
    orchestrator = MagicMock()
    orchestrator.router.local.complete.return_value = CompletionResult(
        text=reply_text, tier=Tier.LOCAL, model="fake", input_tokens=1, output_tokens=1,
    )
    loop.orchestrator = orchestrator
    return loop


def test_addressed_utterance_passes():
    loop = _make_loop_with_local_reply("ADDRESSED")
    assert loop._seems_addressed_to_argus("what's the weather like") is True


def test_stray_utterance_is_flagged():
    loop = _make_loop_with_local_reply("STRAY")
    assert loop._seems_addressed_to_argus("okay yeah all right thanks") is False


def test_local_model_failure_fails_open():
    loop = VoiceLoop.__new__(VoiceLoop)
    orchestrator = MagicMock()
    orchestrator.router.local.complete.side_effect = RuntimeError("ollama down")
    loop.orchestrator = orchestrator
    # never silently drop a real request just because the check itself broke
    assert loop._seems_addressed_to_argus("anything") is True
