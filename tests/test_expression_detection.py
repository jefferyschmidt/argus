from argus.orchestrator import _detect_requested_expression


def test_specific_expression_request():
    assert _detect_requested_expression("show me an angry face", None) == "angry"
    assert _detect_requested_expression("can you make a scared expression", None) == "scared"
    assert _detect_requested_expression("show happy", None) is None  # no face/expression word -- not a real request


def test_generic_request_starts_the_cycle():
    assert _detect_requested_expression("show me some facial expressions", None) == "happy"
    assert _detect_requested_expression("I want to see your emotions", None) == "happy"


def test_generic_request_cycles_from_last_shown():
    assert _detect_requested_expression("show me another expression", "happy") == "angry"
    assert _detect_requested_expression("show me another expression", "surprised") == "happy"


def test_unrelated_text_detects_nothing():
    assert _detect_requested_expression("what's the weather like", None) is None
    assert _detect_requested_expression("I'm feeling pretty happy today", None) is None
