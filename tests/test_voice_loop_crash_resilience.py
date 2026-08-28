from unittest.mock import MagicMock

from argus.ui import commands as ui_commands
from argus.voice.loop import VoiceLoop


def _loop_with_failing_orchestrator(exc: Exception) -> tuple[VoiceLoop, MagicMock]:
    loop = VoiceLoop.__new__(VoiceLoop)  # bypass __init__ -- no mic/models needed
    loop.orchestrator = MagicMock()
    loop.orchestrator.handle_streaming.side_effect = exc
    loop.orchestrator.last_tier = None
    loop.transcriber = MagicMock()
    speak_fn = MagicMock(return_value=False)
    loop._speak_with_barge_in = speak_fn
    return loop, speak_fn


def test_unexpected_exception_during_a_turn_does_not_propagate():
    """Confirmed live: an uncaught exception from the orchestrator (a real
    Anthropic BadRequestError, in the incident that prompted this) took
    down the entire `argus voice` process, not just that one turn -- the
    only try/except around handle_streaming caught _BargeInInterrupt
    specifically, nothing broader. A single bad turn must be recoverable."""
    loop, speak_fn = _loop_with_failing_orchestrator(RuntimeError("boom"))
    ui_commands.set_quiet_mode(False)

    result = loop._process_utterance(text="can you look at the camera")  # must not raise

    assert result is True  # session stays alive, follow-up window stays open
    speak_fn.assert_called_once()
    assert "went wrong" in speak_fn.call_args[0][0]


def test_barge_in_interrupt_is_still_handled_silently():
    """The pre-existing behavior (a real barge-in mid-reply) must not
    start reporting a spurious error now that a broader except was added
    alongside it."""
    from argus.voice.loop import _BargeInInterrupt

    loop, speak_fn = _loop_with_failing_orchestrator(_BargeInInterrupt())
    ui_commands.set_quiet_mode(False)

    result = loop._process_utterance(text="can you look at the camera")

    assert result is True
    speak_fn.assert_not_called()
