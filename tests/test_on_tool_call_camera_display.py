from unittest.mock import MagicMock, patch

from argus.orchestrator import Orchestrator


def _orchestrator():
    orch = Orchestrator.__new__(Orchestrator)
    orch.tools = MagicMock()
    orch.tools._tools = {}
    return orch


def test_capture_camera_bytes_result_skips_the_generic_auto_display():
    """capture_camera publishes its own display event (stylized by
    default) -- the generic auto-display here must be skipped for it, or
    the raw photo would ALSO silently show up, defeating that default."""
    orch = _orchestrator()

    with patch("argus.orchestrator.ui_events.publish") as mock_publish:
        orch._on_tool_call("capture_camera", {}, b"\xff\xd8fake-jpeg-bytes")

    event = mock_publish.call_args[0][0]
    assert "image" not in event
    assert event["result"] == "<17 bytes>"


def test_other_bytes_results_still_get_the_generic_auto_display():
    orch = _orchestrator()

    with patch("argus.orchestrator.ui_events.publish") as mock_publish:
        orch._on_tool_call("take_screenshot", {}, b"\x89PNGfake-bytes")

    event = mock_publish.call_args[0][0]
    assert "image" in event
