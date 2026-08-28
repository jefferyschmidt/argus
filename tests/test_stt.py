from unittest.mock import MagicMock

import numpy as np

from argus.voice.stt import Transcriber, _to_wav_bytes


def _samples():
    return (np.sin(2 * np.pi * 220 * np.arange(8000) / 16000) * 3000).astype(np.int16)


def test_empty_samples_return_empty_string_without_calling_any_backend():
    t = Transcriber.__new__(Transcriber)
    t._groq = MagicMock()
    t._local_model = None
    assert t.transcribe(np.array([], dtype=np.int16)) == ""
    t._groq.audio.transcriptions.create.assert_not_called()


def test_uses_groq_when_configured():
    t = Transcriber.__new__(Transcriber)
    t._groq = MagicMock()
    t._groq.audio.transcriptions.create.return_value = MagicMock(text="hello there")

    result = t.transcribe(_samples())

    assert result == "hello there"
    t._groq.audio.transcriptions.create.assert_called_once()


def test_falls_back_to_local_when_groq_raises():
    t = Transcriber.__new__(Transcriber)
    t._groq = MagicMock()
    t._groq.audio.transcriptions.create.side_effect = RuntimeError("network down")
    fake_segment = MagicMock(text="fallback text")
    t._local_model = MagicMock()
    t._local_model.transcribe.return_value = ([fake_segment], None)

    result = t.transcribe(_samples())

    assert result == "fallback text"


def test_to_wav_bytes_produces_a_valid_wav_header():
    wav_bytes = _to_wav_bytes(_samples(), sample_rate=16000)
    assert wav_bytes[:4] == b"RIFF"
    assert wav_bytes[8:12] == b"WAVE"
