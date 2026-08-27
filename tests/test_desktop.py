from unittest.mock import patch

from argus.tools.desktop import _click


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
