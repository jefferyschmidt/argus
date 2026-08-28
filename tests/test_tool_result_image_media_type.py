from argus.llm.anthropic_client import _image_media_type, _tool_result_content


def test_detects_jpeg_from_magic_bytes():
    """capture_camera returns JPEG (cv2.imencode(".jpg", ...)) -- confirmed
    live as a real crashing bug: this used to be sent to the Anthropic API
    hardcoded as image/png, which the API flatly rejects on a mismatch."""
    jpeg_bytes = b"\xff\xd8\xff\xe0" + b"rest of a real jpeg would go here"
    assert _image_media_type(jpeg_bytes) == "image/jpeg"


def test_detects_png_from_magic_bytes():
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"rest of a real png would go here"
    assert _image_media_type(png_bytes) == "image/png"


def test_tool_result_content_uses_the_sniffed_media_type_for_jpeg():
    jpeg_bytes = b"\xff\xd8\xff\xe0fake"
    content = _tool_result_content(jpeg_bytes)
    assert content[0]["source"]["media_type"] == "image/jpeg"


def test_tool_result_content_uses_the_sniffed_media_type_for_png():
    png_bytes = b"\x89PNG\r\n\x1a\nfake"
    content = _tool_result_content(png_bytes)
    assert content[0]["source"]["media_type"] == "image/png"


def test_non_bytes_result_is_unaffected():
    assert _tool_result_content("plain text result") == "plain text result"
