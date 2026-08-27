from argus.voice.sentence_splitter import SentenceBuffer


def test_no_sentence_yielded_until_boundary():
    buf = SentenceBuffer()
    assert buf.add("Hello there") == []


def test_yields_complete_sentence_on_boundary():
    buf = SentenceBuffer()
    buf.add("Hello there.")
    result = buf.add(" How are you")
    assert result == ["Hello there."]


def test_multiple_sentences_in_one_delta():
    buf = SentenceBuffer()
    result = buf.add("First one. Second one. Third pending")
    assert result == ["First one.", "Second one."]


def test_flush_returns_remaining_partial_text():
    buf = SentenceBuffer()
    buf.add("This is incomplete")
    assert buf.flush() == "This is incomplete"


def test_flush_returns_none_when_empty():
    buf = SentenceBuffer()
    buf.add("Complete.")
    buf.flush()  # drain the leftover empty string after the split
    assert buf.flush() is None


def test_incremental_token_by_token_streaming():
    buf = SentenceBuffer()
    tokens = ["Hi", " there", ".", " How", " are", " you", "?"]
    collected = []
    for t in tokens:
        collected.extend(buf.add(t))
    tail = buf.flush()
    if tail:
        collected.append(tail)
    assert collected == ["Hi there.", "How are you?"]
