from argus.orchestrator import _extract_markers


def test_no_markers():
    body, core, expr = _extract_markers("Just a normal reply.")
    assert body == "Just a normal reply."
    assert core is None
    assert expr is None


def test_core_memory_marker_only():
    body, core, expr = _extract_markers("Got it.\nCORE_MEMORY: user prefers dark mode")
    assert body == "Got it."
    assert core == "user prefers dark mode"
    assert expr is None


def test_expression_marker_only():
    body, core, expr = _extract_markers("Here's my angry face.\nEXPRESSION: angry")
    assert body == "Here's my angry face."
    assert core is None
    assert expr == "angry"


def test_both_markers_either_order():
    body, core, expr = _extract_markers(
        "Noted.\nCORE_MEMORY: user's dog is named Rex\nEXPRESSION: happy"
    )
    assert body == "Noted."
    assert core == "user's dog is named Rex"
    assert expr == "happy"

    body2, core2, expr2 = _extract_markers(
        "Noted.\nEXPRESSION: happy\nCORE_MEMORY: user's dog is named Rex"
    )
    assert body2 == "Noted."
    assert core2 == "user's dog is named Rex"
    assert expr2 == "happy"


def test_invalid_expression_name_ignored():
    body, core, expr = _extract_markers("Sure.\nEXPRESSION: bananas")
    assert body == "Sure."
    assert expr is None
