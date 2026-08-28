from unittest.mock import MagicMock, patch

from argus.voice.loop import VoiceLoop, _is_thought
from argus.voice.sentence_splitter import SentenceBuffer


def test_recognizes_a_fully_parenthesized_sentence_as_a_thought():
    assert _is_thought("(Checking the calendar first.)") is True
    assert _is_thought("  (Nested (bits) still count.)  ") is True


def test_ordinary_speech_is_not_a_thought():
    assert _is_thought("Here's what I found.") is False
    assert _is_thought("(Partially parenthesized) but not fully.") is False
    # Starts with "(" and ends with ")" but the first paren closes early --
    # a normal spoken sentence, not a thought.
    assert _is_thought("(a) and (b) are both fine.") is False
    assert _is_thought("()") is False
    assert _is_thought("") is False


def _loop():
    loop = VoiceLoop.__new__(VoiceLoop)
    loop._speak_with_barge_in = MagicMock(return_value=False)
    return loop


def test_a_thought_is_displayed_but_never_spoken():
    loop = _loop()
    with patch("argus.voice.loop.ui_events.publish") as mock_publish:
        interrupted = loop._speak_unless_thought("(The calculator's already open.)")

    assert interrupted is False
    loop._speak_with_barge_in.assert_not_called()
    captions = [c.args[0] for c in mock_publish.call_args_list if c.args[0].get("type") == "caption"]
    assert captions == [{"type": "caption", "text": "(The calculator's already open.)"}]


def test_ordinary_speech_is_still_spoken():
    loop = _loop()
    with patch("argus.voice.loop.ui_events.publish"):
        loop._speak_unless_thought("Four plus four is eight.")

    loop._speak_with_barge_in.assert_called_once_with("Four plus four is eight.")


def test_speak_sentences_skips_thoughts_but_speaks_the_rest():
    loop = _loop()
    with patch("argus.voice.loop.ui_events.publish"):
        remaining = loop._speak_sentences(
            ["(Looking it up.)", "It's eight.", "(Done.)"]
        )

    assert remaining == []
    spoken = [c.args[0] for c in loop._speak_with_barge_in.call_args_list]
    assert spoken == ["It's eight."]


def test_splitter_separates_a_thought_from_the_speech_after_it():
    """A thought only gets recognized if it arrives as its OWN sentence --
    the splitter has to break after the closing paren, not swallow it into
    the following sentence."""
    buf = SentenceBuffer()
    out = buf.add("(Checking the calendar first.) Here's what I found. ")
    assert out == ["(Checking the calendar first.)", "Here's what I found."]


def test_splitter_also_breaks_after_a_closing_quote():
    buf = SentenceBuffer()
    out = buf.add('He said "hello." Then he left. ')
    assert out == ['He said "hello."', "Then he left."]
