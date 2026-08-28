from unittest.mock import MagicMock, patch

from argus.tools.desktop import _capture_camera, _click, _scroll, _stylize_vision


def test_click_rejects_malformed_coordinate_string():
    result = _click({"x": "258, 60", "y": 60})
    assert result.startswith("error:")
    assert "must both be integers" in result


def test_click_rejects_missing_y():
    result = _click({"x": 100})
    assert result.startswith("error:")


def test_click_accepts_valid_integer_coordinates():
    with patch("pyautogui.click") as mock_click:
        result = _click({"x": 100, "y": 200})
    mock_click.assert_called_once_with(100, 200)
    assert result == "clicked at (100, 200)"


def test_click_coerces_numeric_strings():
    with patch("pyautogui.click") as mock_click:
        result = _click({"x": "100", "y": "200"})
    mock_click.assert_called_once_with(100, 200)
    assert result == "clicked at (100, 200)"


def test_scroll_rejects_non_integer_amount():
    result = _scroll({"amount": "a lot"})
    assert result.startswith("error:")


def test_scroll_scrolls_at_current_position_when_no_coordinates_given():
    with patch("pyautogui.scroll") as mock_scroll, patch("pyautogui.moveTo") as mock_move:
        result = _scroll({"amount": -400})
    mock_move.assert_not_called()
    mock_scroll.assert_called_once_with(-400)
    assert result == "scrolled -400"


def test_scroll_moves_to_given_coordinates_first():
    with patch("pyautogui.scroll") as mock_scroll, patch("pyautogui.moveTo") as mock_move:
        result = _scroll({"amount": 200, "x": 500, "y": 300})
    mock_move.assert_called_once_with(500, 300)
    mock_scroll.assert_called_once_with(200)
    assert result == "scrolled 200"


def test_capture_camera_returns_error_when_no_camera_available():
    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = False
    with patch("cv2.VideoCapture", return_value=mock_cap):
        result = _capture_camera({})
    assert result.startswith("error:")
    assert "no camera" in result
    mock_cap.release.assert_called_once()


def test_capture_camera_returns_error_when_read_fails():
    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = True
    mock_cap.read.return_value = (False, None)
    with patch("cv2.VideoCapture", return_value=mock_cap):
        result = _capture_camera({})
    assert result.startswith("error:")
    assert "failed to capture" in result
    mock_cap.release.assert_called_once()


def test_capture_camera_returns_jpeg_bytes_on_success():
    import numpy as np

    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = True
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    mock_cap.read.return_value = (True, frame)
    with patch("cv2.VideoCapture", return_value=mock_cap), \
         patch("argus.tools.desktop.ui_events.publish"):
        result = _capture_camera({})
    assert isinstance(result, bytes)
    assert result[:2] == b"\xff\xd8"  # JPEG magic bytes
    mock_cap.release.assert_called_once()


def test_stylize_vision_returns_same_shape_black_canvas_with_edges():
    import numpy as np

    frame = np.zeros((20, 20, 3), dtype=np.uint8)
    frame[5:15, 5:15] = 255  # a bright square, should produce an edge ring

    result = _stylize_vision(frame)

    assert result.shape == frame.shape
    assert result.any()  # some edge pixels were drawn, not an all-black frame


def test_capture_camera_publishes_stylized_view_by_default_not_raw():
    """Confirmed directly requested: show a computer-vision-style rendering
    by default, never the literal photo unless specifically asked."""
    import numpy as np

    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = True
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    mock_cap.read.return_value = (True, frame)

    with patch("cv2.VideoCapture", return_value=mock_cap), \
         patch("argus.tools.desktop.ui_events.publish") as mock_publish:
        _capture_camera({})

    mock_publish.assert_called_once()
    event = mock_publish.call_args[0][0]
    assert event["type"] == "tool_call"
    assert event["name"] == "camera view (computer vision)"
    assert "image" in event


def test_capture_camera_publishes_raw_view_when_explicitly_requested():
    import numpy as np

    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = True
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    mock_cap.read.return_value = (True, frame)

    with patch("cv2.VideoCapture", return_value=mock_cap), \
         patch("argus.tools.desktop.ui_events.publish") as mock_publish:
        _capture_camera({"raw": True})

    event = mock_publish.call_args[0][0]
    assert event["name"] == "capture_camera"


def test_capture_camera_returns_the_real_frame_to_the_model_even_when_stylized_for_display():
    """The stylized rendering is a display-only choice -- the model must
    still see and be able to analyze the actual captured frame either way,
    e.g. to answer "what am I holding"."""
    import cv2
    import numpy as np

    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = True
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    frame[:] = (10, 20, 30)  # a distinctive, non-black frame
    mock_cap.read.return_value = (True, frame)

    with patch("cv2.VideoCapture", return_value=mock_cap), \
         patch("argus.tools.desktop.ui_events.publish"):
        result = _capture_camera({})

    decoded = cv2.imdecode(np.frombuffer(result, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert decoded is not None
    assert tuple(decoded[0, 0]) != (0, 0, 0)  # the real (non-black) frame, not the stylized one
