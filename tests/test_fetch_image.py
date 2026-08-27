import io
from unittest.mock import MagicMock, patch

from PIL import Image

from argus.tools.web_content import _fetch_image


def _fake_png_bytes() -> bytes:
    img = Image.new("RGB", (4, 4), color="red")
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


def test_successful_fetch_returns_png_bytes():
    png = _fake_png_bytes()
    resp = MagicMock()
    resp.headers = {"Content-Type": "image/jpeg"}
    resp.read.return_value = png
    resp.__enter__.return_value = resp
    with patch("urllib.request.urlopen", return_value=resp):
        result = _fetch_image({"url": "https://example.com/photo.jpg"})
    assert isinstance(result, bytes)
    img = Image.open(io.BytesIO(result))
    assert img.format == "PNG"


def test_oversized_image_rejected():
    resp = MagicMock()
    resp.headers = {"Content-Type": "image/png"}
    resp.read.return_value = b"x" * (10_000_000 + 1)
    resp.__enter__.return_value = resp
    with patch("urllib.request.urlopen", return_value=resp):
        result = _fetch_image({"url": "https://example.com/huge.png"})
    assert result.startswith("error:")
    assert "too large" in result


def test_network_failure_returns_error_string():
    with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
        result = _fetch_image({"url": "https://example.com/photo.jpg"})
    assert result.startswith("error:")
