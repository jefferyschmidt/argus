from unittest.mock import MagicMock, patch

from argus.llm.base import CompletionResult, Tier
from argus.tools.scan_document import _build_scan_document


def _router(text):
    router = MagicMock()
    router.complete_with_image.return_value = CompletionResult(text=text, tier=Tier.FAST, model="test")
    return router


def _mock_cap(opened=True, read_ok=True, frame=None):
    import numpy as np

    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = opened
    mock_cap.read.return_value = (read_ok, frame if frame is not None else np.zeros((10, 10, 3), dtype="uint8"))
    return mock_cap


def test_no_camera_available_returns_error():
    tool = _build_scan_document(_router("irrelevant"))
    with patch("cv2.VideoCapture", return_value=_mock_cap(opened=False)):
        result = tool.handler({})
    assert result.startswith("error:")
    assert "no camera" in result


def test_failed_frame_capture_returns_error():
    tool = _build_scan_document(_router("irrelevant"))
    with patch("cv2.VideoCapture", return_value=_mock_cap(read_ok=False)):
        result = tool.handler({})
    assert result.startswith("error:")
    assert "failed to capture" in result


def test_successful_scan_stores_and_returns_extraction():
    router = _router("Receipt from Trader Joe's, $47.32, dated 2026-08-27.")
    with patch("cv2.VideoCapture", return_value=_mock_cap()), \
         patch("argus.tools.scan_document.SemanticStore") as store_cls:
        store = store_cls.return_value
        tool = _build_scan_document(router)
        result = tool.handler({})

    assert result == "Receipt from Trader Joe's, $47.32, dated 2026-08-27."
    store.add.assert_called_once()
    call = store.add.call_args
    assert call.kwargs["text"] == result
    assert call.kwargs["metadata"]["source"] == "document_scan"


def test_vision_call_uses_fast_tier():
    router = _router("some receipt")
    with patch("cv2.VideoCapture", return_value=_mock_cap()), patch("argus.tools.scan_document.SemanticStore"):
        tool = _build_scan_document(router)
        tool.handler({})

    assert router.complete_with_image.call_args.kwargs["tier"] == Tier.FAST


def test_vision_failure_returns_error():
    router = MagicMock()
    router.complete_with_image.side_effect = RuntimeError("boom")
    tool = _build_scan_document(router)

    with patch("cv2.VideoCapture", return_value=_mock_cap()):
        result = tool.handler({})

    assert result.startswith("error:")


def test_empty_extraction_suggests_retrying():
    router = _router("   ")
    with patch("cv2.VideoCapture", return_value=_mock_cap()):
        tool = _build_scan_document(router)
        result = tool.handler({})

    assert "steadier" in result or "closer" in result
