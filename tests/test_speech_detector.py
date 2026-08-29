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
    tiny = np.zeros(10, dtype=np.int16)  # shorter than one 512-sample sub-chunk
    assert detector.is_speech(tiny) is False


def test_rejects_non_16khz():
    detector = SpeechDetector()
    frame = np.zeros(1280, dtype=np.int16)
    try:
        detector.is_speech(frame, sample_rate=8000)
        assert False, "expected ValueError for non-16kHz input"
    except ValueError:
        pass


def test_frame_length_matches_codebase_chunking():
    """Both the 1280-sample (80ms @16kHz) barge-in chunk size and shorter
    partial frames should be handled without raising."""
    detector = SpeechDetector()
    for length in (512, 1024, 1280):
        frame = _tone(200.0, length / 16000, amplitude=5000)[:length]
        detector.is_speech(frame)  # must not raise


def test_reset_does_not_raise():
    detector = SpeechDetector()
    detector.is_speech(np.zeros(1280, dtype=np.int16))
    detector.reset()
    detector.is_speech(np.zeros(1280, dtype=np.int16))


def test_short_utterance_with_heavy_trailing_silence_is_still_speech():
    """Regression test for a real, recurring live complaint ("he doesn't
    understand half of what I say"): record_followup always appends a
    fixed ~900ms trailing silence-hang to every capture, so a short real
    utterance is easily <50% speech-flagged frames overall even though
    it's genuine speech. The old majority-vote rule rejected this; the
    fix (an absolute floor on speech-flagged chunks) must accept it."""
    detector = SpeechDetector()
    # Fake model: first 5 of 40 sub-chunks (160ms) "speech", rest silence --
    # 12.5% overall, comfortably below the old 50% majority threshold but
    # a real, sustained run of speech-flagged frames.
    calls = {"n": 0}

    def fake_model(_sub, _sr):
        calls["n"] += 1
        return type("P", (), {"item": lambda self: 0.9 if calls["n"] <= 5 else 0.0})()

    detector._model = fake_model
    frame = np.zeros(512 * 40, dtype=np.int16)
    assert detector.is_speech(frame) is True


def test_single_chunk_frame_still_works_for_real_time_wake_word_detection():
    """Regression test for a real regression the fix above introduced: a
    FIXED absolute floor broke local_wake_word.py's passive listener,
    which calls is_speech() once per single 32ms frame (exactly one
    Silero sub-chunk, total == 1) to decide moment-to-moment whether
    speech has started. A fixed floor of 4 can never be reached when
    total is 1, so wake-word detection died completely -- confirmed live
    ("doesn't pick me up at all," while push-to-talk, a separate RMS-only
    path, kept working). The floor must be capped at the call's own
    total so a single speech-flagged chunk is still enough on its own."""
    detector = SpeechDetector()

    def speech_model(_sub, _sr):
        return type("P", (), {"item": lambda self: 0.95})()

    detector._model = speech_model
    frame = np.zeros(512, dtype=np.int16)  # exactly one 32ms sub-chunk
    assert detector.is_speech(frame) is True
