from argus.llm.base import Tier
from argus.llm.router import classify


def test_classify_short_trivial_message_is_local():
    assert classify("hi") is Tier.LOCAL


def test_classify_message_with_advanced_keyword_is_advanced():
    assert classify("can you review this design and debug the issue") is Tier.ADVANCED


def test_classify_medium_message_is_fast():
    text = "what's a good way to phrase this email to my landlord about the leak"
    assert classify(text) is Tier.FAST
