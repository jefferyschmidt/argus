from argus.voice.local_wake_word import _find_wake_word


def test_exact_wake_word_still_matches_via_the_fast_path():
    assert _find_wake_word("argus what time is it") is not None
    assert _find_wake_word("hey argos can you help") is not None


def test_fuzzy_fallback_catches_a_near_miss_transcription():
    """Confirmed live as a real stuck-listening bug: local Whisper (already
    documented as prone to mishearing "Argus" on short clips) sometimes
    produces something close but not in the exact word list, and the
    utterance then failed to match forever with no way out."""
    assert _find_wake_word("argue, I said yes, handle that") is not None


def test_unrelated_word_does_not_false_trigger():
    assert _find_wake_word("the argument was long") is None
    assert _find_wake_word("just circus tricks today") is None


def test_no_wake_word_at_all_returns_none():
    assert _find_wake_word("what time is it") is None


def test_returned_index_lets_caller_slice_out_the_command():
    end_idx = _find_wake_word("argus what time is it")
    assert "what time is it" in "argus what time is it"[end_idx:]
