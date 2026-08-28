import numpy as np

from argus.voice.speech_detector import SpeechDetector


def _tone(freq_hz: float, duration_s: float, sample_rate: int = 16000, amplitude: int = 8000) -> np.ndarray:
    t = np.arange(int(sample_rate * duration_s)) / sample_rate
    return (np.sin(2 * np.pi * freq_hz * t) * amplitude).astype(np.int16)


def test_silence_is_not_speech():
    detector = SpeechDetector()
    silence = np.zeros(1280, dtype=np.int16)  # 80ms at 16kHz
    assert detector.is_speech(silence) is False


def test_too_short_frame_is_not_speech():
    detector = SpeechDetector()
    tiny = np.zeros(10, dtype=np.int16)  # shorter than one 20ms sub-frame
    assert detector.is_speech(tiny) is False


def test_frame_length_matches_codebase_chunking():
    """Both the 1280-sample (80ms @16kHz) barge-in chunk size and shorter
    partial frames should be handled without raising."""
    detector = SpeechDetector()
    for length in (320, 640, 1280):
        frame = _tone(200.0, length / 16000, amplitude=5000)[:length]
        detector.is_speech(frame)  # must not raise
