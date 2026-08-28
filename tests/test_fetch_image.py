import io
from unittest.mock import MagicMock, patch

from PIL import Image

from argus.tools.web_content import _close_show_window, _fetch_image, _show_website


def _fake_source_bytes(size=(4, 4)) -> bytes:
    img = Image.new("RGB", size, color="red")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_rejects_non_http_scheme():
    result = _fetch_image({"url": "file:///etc/passwd"})
    assert result.startswith("error:")
    assert "http" in result


def test_rejects_non_image_content_type():
    resp = MagicMock()
    resp.headers = {"Content-Type": "text/html"}
    resp.__enter__.return_value = resp
    with patch("urllib.request.urlopen", return_value=resp):
        result = _fetch_image({"url": "https://example.com/page.html"})
    assert result.startswith("error:")
    assert "content-type" in result


def test_successful_fetch_returns_bounded_jpeg_bytes():
    source = _fake_source_bytes()
    resp = MagicMock()
    resp.headers = {"Content-Type": "image/jpeg"}
    resp.read.return_value = source
    resp.__enter__.return_value = resp
    with patch("urllib.request.urlopen", return_value=resp), \
         patch("argus.tools.web_content.ui_events.publish"):
        result = _fetch_image({"url": "https://example.com/photo.jpg"})
    assert isinstance(result, bytes)
    img = Image.open(io.BytesIO(result))
    assert img.format == "JPEG"


def test_large_dimensions_are_downscaled():
    source = _fake_source_bytes(size=(3000, 2000))
    resp = MagicMock()
    resp.headers = {"Content-Type": "image/png"}
    resp.read.return_value = source
    resp.__enter__.return_value = resp
    with patch("urllib.request.urlopen", return_value=resp), \
         patch("argus.tools.web_content.ui_events.publish"):
        result = _fetch_image({"url": "https://example.com/huge-dims.png"})
    img = Image.open(io.BytesIO(result))
    assert max(img.size) <= 1568


def test_successful_fetch_publishes_a_show_modal_event():
    """Confirmed directly requested: "show me a picture of X" should open
    a large show window, not just add a small thumbnail."""
    source = _fake_source_bytes()
    resp = MagicMock()
    resp.headers = {"Content-Type": "image/jpeg"}
    resp.read.return_value = source
    resp.__enter__.return_value = resp
    with patch("urllib.request.urlopen", return_value=resp), \
         patch("argus.tools.web_content.ui_events.publish") as mock_publish:
        _fetch_image({"url": "https://example.com/photo.jpg", "title": "a 1966 Mustang"})

    mock_publish.assert_called_once()
    event = mock_publish.call_args[0][0]
    assert event == {
        "type": "show_modal", "kind": "image", "title": "a 1966 Mustang", "image": event["image"],
    }


def test_fetch_image_title_defaults_to_the_url_filename():
    source = _fake_source_bytes()
    resp = MagicMock()
    resp.headers = {"Content-Type": "image/jpeg"}
    resp.read.return_value = source
    resp.__enter__.return_value = resp
    with patch("urllib.request.urlopen", return_value=resp), \
         patch("argus.tools.web_content.ui_events.publish") as mock_publish:
        _fetch_image({"url": "https://example.com/mustang.jpg"})

    assert mock_publish.call_args[0][0]["title"] == "mustang.jpg"


def test_show_website_rejects_non_http_scheme():
    result = _show_website({"url": "file:///etc/passwd"})
    assert result.startswith("error:")


def test_show_website_publishes_a_url_show_modal_event():
    with patch("argus.tools.web_content.ui_events.publish") as mock_publish:
        result = _show_website({"url": "https://example.com"})

    assert "example.com" in result
    mock_publish.assert_called_once_with({
        "type": "show_modal", "kind": "url", "url": "https://example.com", "title": "https://example.com",
    })


def test_show_website_uses_explicit_title_when_given():
    with patch("argus.tools.web_content.ui_events.publish") as mock_publish:
        _show_website({"url": "https://example.com", "title": "Example Site"})

    assert mock_publish.call_args[0][0]["title"] == "Example Site"


def test_oversized_download_rejected():
    resp = MagicMock()
    resp.headers = {"Content-Type": "image/png"}
    resp.read.return_value = b"x" * (15_000_000 + 1)
    resp.__enter__.return_value = resp
    with patch("urllib.request.urlopen", return_value=resp):
        result = _fetch_image({"url": "https://example.com/huge.png"})
    assert result.startswith("error:")
    assert "too large" in result


def test_network_failure_returns_error_string():
    with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
        result = _fetch_image({"url": "https://example.com/photo.jpg"})
    assert result.startswith("error:")


def test_close_show_window_publishes_a_close_event():
    """Confirmed live as a real gap: no way to close the show window
    except a manual click in the console itself."""
    with patch("argus.tools.web_content.ui_events.publish") as mock_publish:
        result = _close_show_window({})

    mock_publish.assert_called_once_with({"type": "show_modal_close"})
    assert "Closed" in result
