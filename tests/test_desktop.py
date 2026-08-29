from unittest.mock import MagicMock, patch

from argus.tools.desktop import (
    _capture_camera,
    _click,
    _detect_faces,
    _draw_target_reticle,
    _list_ui_elements,
    _scroll,
    _stylize_vision,
)


def _mock_element(name="", control_type="Button", rect=(10, 10, 110, 40), visible=True, enabled=True):
    el = MagicMock()
    el.window_text.return_value = name
    el.element_info.control_type = control_type
    el.is_visible.return_value = visible
    el.is_enabled.return_value = enabled
    rect_mock = MagicMock()
    x0, y0, x1, y1 = rect
    rect_mock.width.return_value = x1 - x0
    rect_mock.height.return_value = y1 - y0
    rect_mock.mid_point.return_value = ((x0 + x1) // 2, (y0 + y1) // 2)
    el.rectangle.return_value = rect_mock
    return el


def _mock_desktop(elements):
    window = MagicMock()
    window.descendants.return_value = elements
    desktop_cls = MagicMock()
    desktop_cls.return_value.window.return_value = window
    return desktop_cls


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
    assert result.dtype == frame.dtype
    assert result.any()  # some edge pixels were drawn, not an all-black frame


def test_detect_faces_on_a_blank_frame_returns_no_faces_without_raising():
    """Confirmed directly requested: make the rendering "cooler" -- a
    corner-bracket targeting reticle on any detected face, using the
    Haar cascade already bundled with opencv (no extra model/download).
    Must never break the whole rendering if detection finds nothing or
    fails for any reason."""
    import numpy as np

    gray = np.zeros((50, 50), dtype=np.uint8)
    faces = _detect_faces(gray)
    assert faces == []


def test_draw_target_reticle_draws_visible_pixels_in_its_region():
    import numpy as np

    canvas = np.zeros((100, 100, 3), dtype=np.uint8)
    _draw_target_reticle(canvas, 20, 20, 40, 40)
    assert canvas.any()




def test_capture_camera_publishes_stylized_view_by_default_not_raw():
    """Confirmed directly requested: show a computer-vision-style rendering
    by default, never the literal photo unless specifically asked. Also
    confirmed live: this must use the large show window (same as
    fetch_image/show_website), not the small incidental-capture strip --
    "show me what you see on the camera" is unambiguously a "show me"
    request."""
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
    assert event["type"] == "show_modal"
    assert event["kind"] == "image"
    assert "computer vision" in event["title"]
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
    assert "computer vision" not in event["title"]


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


def test_list_ui_elements_returns_labeled_targets_with_coordinates():
    els = [
        _mock_element(name="Delete", control_type="Button", rect=(100, 200, 160, 230)),
        _mock_element(name="", control_type="Pane", rect=(0, 0, 1000, 1000)),  # unlabeled noise, skipped
    ]
    with patch("pywinauto.Desktop", _mock_desktop(els)):
        result = _list_ui_elements({})

    assert '[1] Button "Delete" at (130, 215)' in result
    assert "Pane" not in result


def test_list_ui_elements_skips_invisible_and_disabled_elements():
    els = [
        _mock_element(name="Hidden", visible=False),
        _mock_element(name="Disabled", enabled=False),
        _mock_element(name="Visible", rect=(0, 0, 50, 20)),
    ]
    with patch("pywinauto.Desktop", _mock_desktop(els)):
        result = _list_ui_elements({})

    assert "Hidden" not in result
    assert "Disabled" not in result
    assert "Visible" in result


def test_list_ui_elements_skips_zero_size_elements():
    els = [_mock_element(name="Zero", rect=(10, 10, 10, 10))]
    with patch("pywinauto.Desktop", _mock_desktop(els)):
        result = _list_ui_elements({})

    assert "Zero" not in result


def test_list_ui_elements_reports_when_nothing_found():
    with patch("pywinauto.Desktop", _mock_desktop([])):
        result = _list_ui_elements({})

    assert "No labeled UI elements found" in result


def test_list_ui_elements_reports_error_when_no_active_window():
    desktop_cls = MagicMock()
    desktop_cls.return_value.window.side_effect = RuntimeError("no active window")
    with patch("pywinauto.Desktop", desktop_cls):
        result = _list_ui_elements({})

    assert result.startswith("error:")


def test_list_ui_elements_one_flaky_element_does_not_break_the_whole_listing():
    good = _mock_element(name="Good", rect=(0, 0, 50, 20))
    bad = _mock_element(name="Bad")
    bad.rectangle.side_effect = RuntimeError("boom")
    with patch("pywinauto.Desktop", _mock_desktop([bad, good])):
        result = _list_ui_elements({})

    assert "Good" in result
