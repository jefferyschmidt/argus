"""PRD.md §15 unit 32: the shared deterministic acknowledgment check
both voice loops call after their own confirmation-diversion has already
run (or, for voice/loop.py, been made structurally unreachable by
_interaction_lock -- see that call site)."""

from unittest.mock import MagicMock

from argus.voice.acknowledgment import (
    ACKNOWLEDGMENT_PHRASES,
    is_acknowledgment_phrase,
    maybe_acknowledge_spoken_thread,
)


def test_recognizes_each_listed_phrase():
    for phrase in ACKNOWLEDGMENT_PHRASES:
        assert is_acknowledgment_phrase(phrase)
        assert is_acknowledgment_phrase(f"yeah, {phrase}")


def test_bare_yes_is_never_an_acknowledgment_phrase():
    """§15 unit 32, non-negotiable: deliberately not reusing _YES_WORDS.
    A bare "yes" answering some unrelated question must not silently
    close a thread."""
    for word in ("yes", "yeah", "yep", "sure", "okay", "ok"):
        assert not is_acknowledgment_phrase(word)


def test_unrelated_text_is_not_an_acknowledgment_phrase():
    assert not is_acknowledgment_phrase("what's the weather like today")


def _engine(thread_id=42, spoken_ts=1_000_000.0, acknowledge_result=True):
    engine = MagicMock()
    engine.dispatcher.last_spoken_thread_id = thread_id
    engine.dispatcher.last_spoken_ts = spoken_ts
    engine.acknowledge_thread.return_value = acknowledge_result
    return engine


def test_matching_phrase_within_window_acknowledges_the_spoken_thread():
    engine = _engine(thread_id=42, spoken_ts=1_000_000.0)

    result = maybe_acknowledge_spoken_thread("got it", engine, now=1_000_010.0)

    assert result is True
    engine.acknowledge_thread.assert_called_once_with(42, via="voice")


def test_no_recently_spoken_thread_does_nothing():
    engine = _engine(thread_id=None, spoken_ts=None)

    result = maybe_acknowledge_spoken_thread("got it", engine, now=1_000_010.0)

    assert result is False
    engine.acknowledge_thread.assert_not_called()


def test_expired_window_does_nothing():
    from argus.config import settings

    engine = _engine(thread_id=42, spoken_ts=1_000_000.0)
    now = 1_000_000.0 + settings.acknowledgment_window_seconds + 1

    result = maybe_acknowledge_spoken_thread("got it", engine, now=now)

    assert result is False
    engine.acknowledge_thread.assert_not_called()


def test_right_at_the_window_boundary_still_fires():
    from argus.config import settings

    engine = _engine(thread_id=42, spoken_ts=1_000_000.0)
    now = 1_000_000.0 + settings.acknowledgment_window_seconds

    result = maybe_acknowledge_spoken_thread("got it", engine, now=now)

    assert result is True


def test_non_matching_phrase_never_touches_the_engine():
    """Must short-circuit before touching proactive_engine at all --
    both call sites rely on this so a partially-constructed test double
    with no .proactive attribute doesn't blow up on an unrelated
    transcript."""
    engine = _engine()

    result = maybe_acknowledge_spoken_thread("what's the weather like", engine, now=1_000_010.0)

    assert result is False
    engine.acknowledge_thread.assert_not_called()


def test_none_engine_is_a_safe_no_op():
    assert maybe_acknowledge_spoken_thread("got it", None, now=1_000_010.0) is False


def test_bare_yes_never_acknowledges_even_with_an_eligible_thread():
    engine = _engine(thread_id=42, spoken_ts=1_000_000.0)

    result = maybe_acknowledge_spoken_thread("yes", engine, now=1_000_010.0)

    assert result is False
    engine.acknowledge_thread.assert_not_called()


def test_return_value_reflects_whether_the_thread_actually_closed():
    """acknowledge_thread can return False (unknown thread id, a race) --
    that must propagate, not be swallowed into a bare True."""
    engine = _engine(thread_id=42, spoken_ts=1_000_000.0, acknowledge_result=False)

    result = maybe_acknowledge_spoken_thread("got it", engine, now=1_000_010.0)

    assert result is False
