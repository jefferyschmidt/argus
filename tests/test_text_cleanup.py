from argus.voice.text_cleanup import clean_for_speech


def test_strips_bold_and_italic_markers():
    assert clean_for_speech("This is **bold** and *italic* text.") == "This is bold and italic text."


def test_strips_bullets():
    text = "Options:\n- first\n- second\n* third"
    result = clean_for_speech(text)
    assert "-" not in result and "*" not in result
    assert "first" in result and "second" in result and "third" in result


def test_strips_headers_and_code_fences():
    text = "# Title\nSome text.\n```python\nprint('hi')\n```\nMore text."
    result = clean_for_speech(text)
    assert "#" not in result
    assert "```" not in result
    assert "print" not in result


def test_strips_inline_code_keeps_content():
    result = clean_for_speech("Run `argus chat` to start.")
    assert result == "Run argus chat to start."


def test_strips_links_keeps_label():
    result = clean_for_speech("See [the docs](https://example.com) for more.")
    assert result == "See the docs for more."


def test_plain_text_passes_through_unchanged():
    assert clean_for_speech("Hello, how are you today?") == "Hello, how are you today?"
