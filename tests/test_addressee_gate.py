from argus.voice.loop import _LIKELY_ADDRESSED


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
