from argus.proactive_none import is_none_reply


def test_exact_none_is_recognized():
    assert is_none_reply("NONE") is True
    assert is_none_reply("none") is True


def test_none_with_trailing_punctuation_is_recognized():
    """Confirmed live as a real bug: a cheap/local model doesn't always
    follow the literal-token instruction precisely -- a trailing period
    fell through the old exact-match check."""
    assert is_none_reply("NONE.") is True
    assert is_none_reply("None!") is True


def test_paraphrased_none_is_recognized():
    """Confirmed live: the model paraphrasing the escape hatch ("nothing
    worth saying") instead of using the literal token got spoken out loud
    verbatim -- observed live as Argus randomly saying "worth saying"
    with no context."""
    assert is_none_reply("There's nothing worth saying right now.") is True
    assert is_none_reply("Nothing genuinely new here.") is True


def test_empty_text_is_none():
    assert is_none_reply("") is True
    assert is_none_reply(None) is True


def test_a_real_reply_is_not_none():
    assert is_none_reply("Sounds like a deep dive -- need any help with those settings?") is False
