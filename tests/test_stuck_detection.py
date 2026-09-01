from unittest.mock import MagicMock, patch

from argus.llm.base import CompletionResult, Tier
from argus.stuck_detection import StuckDetectionWorker


def _worker(reply_text="NONE"):
    router = MagicMock()
    router.complete_with_image.return_value = CompletionResult(text=reply_text, tier=Tier.FAST, model="test")
    dispatcher = MagicMock()
    return StuckDetectionWorker(router, dispatcher), router, dispatcher


def _past_threshold(worker):
    worker._current_since -= 999999


def test_window_switch_resets_and_does_not_assess():
    worker, router, dispatcher = _worker()
    with patch("argus.stuck_detection._active_window_title", return_value="VS Code"):
        worker._scan_once()  # first sighting of this title -- just records it

    router.complete_with_image.assert_not_called()
    dispatcher.submit.assert_not_called()


def test_short_duration_on_same_window_does_not_assess():
    worker, router, dispatcher = _worker()
    worker._current_title = "VS Code"
    worker._current_since = __import__("time").monotonic()  # just started

    with patch("argus.stuck_detection._active_window_title", return_value="VS Code"):
        worker._scan_once()

    router.complete_with_image.assert_not_called()


def test_long_duration_with_none_reply_produces_no_submission():
    worker, router, dispatcher = _worker(reply_text="NONE")
    worker._current_title = "VS Code"
    _past_threshold(worker)

    with patch("argus.stuck_detection._active_window_title", return_value="VS Code"), \
         patch("argus.stuck_detection._screenshot_bytes", return_value=b"fake-png"):
        worker._scan_once()

    router.complete_with_image.assert_called_once()
    dispatcher.submit.assert_not_called()


def test_long_duration_with_real_reply_is_submitted():
    worker, router, dispatcher = _worker(reply_text="Looks like that build's been failing a few times in a row.")
    worker._current_title = "VS Code"
    _past_threshold(worker)

    with patch("argus.stuck_detection._active_window_title", return_value="VS Code"), \
         patch("argus.stuck_detection._screenshot_bytes", return_value=b"fake-png"):
        worker._scan_once()

    dispatcher.submit.assert_called_once()
    (candidate,), _kwargs = dispatcher.submit.call_args
    assert "failing" in candidate.text
    assert candidate.subject == "VS Code"


def test_only_offers_once_per_continuous_window_stretch():
    worker, router, dispatcher = _worker(reply_text="Stuck on that same error for a while now.")
    worker._current_title = "VS Code"
    _past_threshold(worker)

    with patch("argus.stuck_detection._active_window_title", return_value="VS Code"), \
         patch("argus.stuck_detection._screenshot_bytes", return_value=b"fake-png"):
        worker._scan_once()
        worker._scan_once()  # still the same window, past threshold again

    assert dispatcher.submit.call_count == 1
    assert router.complete_with_image.call_count == 1


def test_switching_away_and_back_resets_the_offer_flag():
    worker, router, dispatcher = _worker(reply_text="Stuck on that same error for a while now.")
    worker._current_title = "VS Code"
    _past_threshold(worker)

    with patch("argus.stuck_detection._active_window_title", return_value="VS Code"), \
         patch("argus.stuck_detection._screenshot_bytes", return_value=b"fake-png"):
        worker._scan_once()
    assert dispatcher.submit.call_count == 1

    with patch("argus.stuck_detection._active_window_title", return_value="Slack"):
        worker._scan_once()  # switched away -- resets

    with patch("argus.stuck_detection._active_window_title", return_value="VS Code"):
        worker._scan_once()  # switched back -- also just a registration, not an assessment

    _past_threshold(worker)
    with patch("argus.stuck_detection._active_window_title", return_value="VS Code"), \
         patch("argus.stuck_detection._screenshot_bytes", return_value=b"fake-png"):
        worker._scan_once()  # still on VS Code, now past threshold again

    assert dispatcher.submit.call_count == 2


def test_vision_call_uses_png_media_type():
    """Regression: _screenshot_bytes() (pyautogui.screenshot -> PNG) was
    being sent with the default media_type of "image/jpeg" -- the
    Anthropic API rejects that mismatch outright (confirmed live: a real
    screenshot round-trip failed with a 400 before this was fixed)."""
    worker, router, dispatcher = _worker()
    worker._current_title = "VS Code"
    _past_threshold(worker)

    with patch("argus.stuck_detection._active_window_title", return_value="VS Code"), \
         patch("argus.stuck_detection._screenshot_bytes", return_value=b"fake-png"):
        worker._scan_once()

    assert router.complete_with_image.call_args.kwargs["media_type"] == "image/png"


def test_no_active_window_is_a_safe_noop():
    worker, router, dispatcher = _worker()
    with patch("argus.stuck_detection._active_window_title", return_value=None):
        worker._scan_once()
    dispatcher.submit.assert_not_called()
    router.complete_with_image.assert_not_called()


def test_screenshot_failure_is_a_safe_noop():
    worker, router, dispatcher = _worker()
    worker._current_title = "VS Code"
    _past_threshold(worker)

    with patch("argus.stuck_detection._active_window_title", return_value="VS Code"), \
         patch("argus.stuck_detection._screenshot_bytes", return_value=None):
        worker._scan_once()

    router.complete_with_image.assert_not_called()
    dispatcher.submit.assert_not_called()


def test_vision_failure_is_a_safe_noop():
    worker, router, dispatcher = _worker()
    router.complete_with_image.side_effect = RuntimeError("boom")
    worker._current_title = "VS Code"
    _past_threshold(worker)

    with patch("argus.stuck_detection._active_window_title", return_value="VS Code"), \
         patch("argus.stuck_detection._screenshot_bytes", return_value=b"fake-png"):
        worker._scan_once()  # must not raise

    dispatcher.submit.assert_not_called()
