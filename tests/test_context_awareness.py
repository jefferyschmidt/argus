import threading
from unittest.mock import MagicMock, patch

from argus.context_awareness import ContextAwarenessWorker
from argus.llm.base import CompletionResult, Tier


def _worker(reply_text: str = "NONE"):
    orchestrator = MagicMock()
    orchestrator.router.local.complete.return_value = CompletionResult(
        text=reply_text, tier=Tier.LOCAL, model="test"
    )
    speak_fn = MagicMock()
    lock = threading.Lock()
    worker = ContextAwarenessWorker(orchestrator, speak_fn, lock)
    return worker, speak_fn


def test_model_saying_none_produces_no_delivery():
    worker, speak_fn = _worker(reply_text="NONE")
    with patch("argus.context_awareness._active_window_title", return_value="VS Code - auth.js"):
        worker._scan_once()
    speak_fn.assert_not_called()


def test_real_reply_gets_delivered_with_expression_and_speech():
    worker, speak_fn = _worker(reply_text="Deep in that auth file today, huh?")
    with patch("argus.context_awareness._active_window_title", return_value="VS Code - auth.js"), \
         patch("argus.context_awareness.ui_events.publish") as mock_publish:
        worker._scan_once()

    speak_fn.assert_called_once_with("Deep in that auth file today, huh?")
    published_types = [call.args[0]["type"] for call in mock_publish.call_args_list]
    assert "transcript" in published_types
    assert "expression" in published_types


def test_suppressed_title_never_triggers_generation():
    worker, speak_fn = _worker(reply_text="Should not be seen")
    worker._current_title = "Slack"  # already the active window, no change event this scan
    worker.suppress_current()
    with patch("argus.context_awareness._active_window_title", return_value="Slack"):
        worker._current_since -= 999999  # force the idle-threshold path
        worker._scan_once()
    speak_fn.assert_not_called()


def test_cooldown_blocks_a_second_trigger_immediately_after():
    worker, speak_fn = _worker(reply_text="Jumping around a lot today, huh?")
    with patch("argus.context_awareness._active_window_title", return_value="A"):
        worker._scan_once()
    assert speak_fn.call_count == 1

    with patch("argus.context_awareness._active_window_title", return_value="B"):
        worker._scan_once()  # context changed again immediately, but cooldown should block it
    assert speak_fn.call_count == 1


def test_deliver_skips_when_interaction_lock_is_held():
    orchestrator = MagicMock()
    speak_fn = MagicMock()
    lock = threading.Lock()
    lock.acquire()  # simulate an in-progress conversation
    worker = ContextAwarenessWorker(orchestrator, speak_fn, lock)

    worker._deliver("something")

    speak_fn.assert_not_called()


def test_no_active_window_is_a_safe_noop():
    worker, speak_fn = _worker()
    with patch("argus.context_awareness._active_window_title", return_value=None):
        worker._scan_once()  # must not raise
    speak_fn.assert_not_called()
