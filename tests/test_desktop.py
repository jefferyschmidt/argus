from unittest.mock import MagicMock, patch

from argus.tools.desktop import _capture_camera, _click


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
    with patch("cv2.VideoCapture", return_value=mock_cap):
        result = _capture_camera({})
    assert isinstance(result, bytes)
    assert result[:2] == b"\xff\xd8"  # JPEG magic bytes
    mock_cap.release.assert_called_once()
