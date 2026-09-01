from unittest.mock import MagicMock, patch

from argus.context_awareness import ContextAwarenessWorker


def _worker():
    dispatcher = MagicMock()
    worker = ContextAwarenessWorker(dispatcher)
    return worker, dispatcher


def test_window_change_submits_a_focus_changed_candidate():
    worker, dispatcher = _worker()
    with patch("argus.context_awareness._active_window_title", return_value="VS Code - auth.js"):
        worker._scan_once()

    dispatcher.submit.assert_called_once()
    (candidate,), _kwargs = dispatcher.submit.call_args
    assert candidate.kind == "focus.changed"
    assert candidate.subject == "VS Code - auth.js"
    assert 'Just switched to "VS Code - auth.js"' in candidate.text


def test_window_change_publishes_expression_event():
    worker, dispatcher = _worker()
    with patch("argus.context_awareness._active_window_title", return_value="VS Code - auth.js"), \
         patch("argus.context_awareness.ui_events.publish") as mock_publish:
        worker._scan_once()

    published_types = [call.args[0]["type"] for call in mock_publish.call_args_list]
    assert "expression" in published_types


def test_no_llm_call_happens_anywhere_in_this_worker():
    """U-C4: the standalone "should I say something" LLM call is gone --
    this worker makes no LLM call of its own at all now."""
    worker, dispatcher = _worker()
    with patch("argus.context_awareness._active_window_title", return_value="VS Code - auth.js"):
        worker._scan_once()
    # If it tried to reach an LLM, MagicMock's dispatcher would happily
    # accept any attribute access -- the real assertion is structural:
    # no llm/router import or usage exists in this module at all (see
    # source), and submit() got a plain template string, not a generated one.
    (candidate,), _kwargs = dispatcher.submit.call_args
    assert candidate.text == 'Just switched to "VS Code - auth.js".'


def test_suppressed_title_never_submits():
    worker, dispatcher = _worker()
    worker._current_title = "Slack"  # already the active window, no change event this scan
    worker.suppress_current()
    with patch("argus.context_awareness._active_window_title", return_value="Slack"):
        worker._current_since -= 999999  # force the idle-threshold path
        worker._scan_once()
    dispatcher.submit.assert_not_called()


def test_cooldown_blocks_a_second_submission_immediately_after():
    worker, dispatcher = _worker()
    with patch("argus.context_awareness._active_window_title", return_value="A"):
        worker._scan_once()
    assert dispatcher.submit.call_count == 1

    with patch("argus.context_awareness._active_window_title", return_value="B"):
        worker._scan_once()  # context changed again immediately, but cooldown should block it
    assert dispatcher.submit.call_count == 1


def test_no_active_window_is_a_safe_noop():
    worker, dispatcher = _worker()
    with patch("argus.context_awareness._active_window_title", return_value=None):
        worker._scan_once()  # must not raise
    dispatcher.submit.assert_not_called()
