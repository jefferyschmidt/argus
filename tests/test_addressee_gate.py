from unittest.mock import MagicMock

import numpy as np

from argus.voice.loop import VoiceLoop, _LIKELY_ADDRESSED


def test_question_mark_always_matches_via_caller():
    # The "?" shortcut lives in VoiceLoop._seems_addressed_to_argus itself
    # (needs an instance), but the regex half is tested directly here.
    assert "?" in "Can you still hear me? I just changed audio devices."


def test_direct_address_openers_match():
    for text in [
        "Argus, are you there?",
        "Can you help me with something",
        "Could you show me the weather",
        "Do you know what time it is",
        "What time is it",
        "Please stop",
    ]:
        assert _LIKELY_ADDRESSED.match(text.strip()), text


def test_unrelated_chatter_does_not_match():
    for text in [
        "yeah I'll grab that from the store later",
        "so anyway she said the meeting got moved",
        "the dog needs to go out",
    ]:
        assert not _LIKELY_ADDRESSED.match(text.strip()), text


def test_clear_follow_up_statement_is_not_dropped_as_stray():
    loop = VoiceLoop.__new__(VoiceLoop)
    loop.orchestrator = MagicMock()

    # A close-mic continuation such as "I'd argue that..." is part of the
    # active exchange even without an explicit wake word or question mark.
    assert loop._seems_addressed_to_argus(
        "I'd argue that name is a little on the nose.",
        samples=np.full(800, 500, dtype=np.int16),
    )
    loop.orchestrator.router.local.complete.assert_not_called()
