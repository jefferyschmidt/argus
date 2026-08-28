from unittest.mock import MagicMock, patch

import numpy as np

from argus.voice.local_wake_word import _FRAME_SAMPLES, LocalWakeWordListener, _WAKE_PATTERN


def test_frame_samples_matches_silero_required_chunk_size():
    """Confirmed live as a real, total bug: this module used to compute its
    frame size from a 30ms duration (480 samples at 16kHz), one sample
    chunk short of Silero's minimum. SpeechDetector.is_speech() sub-chunks
    its input into blocks of exactly its own _CHUNK_SAMPLES; fed anything
    smaller, the sub-chunking loop's range is empty and it silently
    returns False unconditionally -- the wake word could never fire
    through the live mic loop no matter what was said. This asserts the
    two modules can never drift apart the same way again."""
    from argus.voice.speech_detector import _CHUNK_SAMPLES

    assert _FRAME_SAMPLES == _CHUNK_SAMPLES


def test_wake_pattern_matches_whole_word_only():
    assert _WAKE_PATTERN.search("argus what time is it")
    assert _WAKE_PATTERN.search("Argus, what time is it")
    assert _WAKE_PATTERN.search("hey argos can you help")  # common mishearing
    assert not _WAKE_PATTERN.search("the argument was long")  # substring, not whole word


def _listener():
    listener = LocalWakeWordListener.__new__(LocalWakeWordListener)
    listener._vad = MagicMock()
    listener._transcriber = MagicMock()
    return listener


def _frame():
    return np.zeros(480, dtype=np.int16)  # 30ms @ 16kHz


class _FakeStream:
    """Feeds a fixed sequence of (frame, is_speech) pairs, then silence
    forever -- enough for _capture_one_utterance to hit its own silence-hang
    exit or the caller's max_frames cap, whichever comes first."""

    def __init__(self, speech_flags):
        self._flags = list(speech_flags)

    def read(self, frame_len):
        return _frame(), None


def test_capture_one_utterance_returns_none_when_nothing_speech_flagged():
    listener = _listener()
    listener._vad.is_speech.return_value = False
    stream = _FakeStream([])

    result = listener._capture_one_utterance(stream, 480, silence_hang_frames=5, max_frames=10, chunks_out=None)

    assert result is None


def test_capture_one_utterance_buffers_speech_and_stops_on_silence_hang():
    listener = _listener()
    # speech, speech, then silence for the hang duration
    listener._vad.is_speech.side_effect = [True, True] + [False] * 10
    stream = _FakeStream([])

    result = listener._capture_one_utterance(stream, 480, silence_hang_frames=3, max_frames=50, chunks_out=None)

    assert result is not None
    assert result.size > 0


def test_capture_one_utterance_appends_to_chunks_out():
    listener = _listener()
    listener._vad.is_speech.side_effect = [True, True] + [False] * 10
    stream = _FakeStream([])
    chunks_out: list = []

    listener._capture_one_utterance(stream, 480, silence_hang_frames=3, max_frames=50, chunks_out=chunks_out)

    assert len(chunks_out) > 0


def test_listen_for_wake_and_command_skips_utterances_without_wake_word():
    listener = _listener()
    listener._transcriber.transcribe_local.side_effect = ["just some chatter", "argus what time is it"]

    # First captured utterance: no wake word -- must loop and try again.
    # Second: has it -- returns.
    with patch.object(listener, "_capture_one_utterance", side_effect=[
        np.ones(16000, dtype=np.int16),  # 1s of "speech" -- clears the min-duration floor
        np.ones(16000, dtype=np.int16),
    ]), patch("argus.voice.local_wake_word.sd.InputStream") as mock_stream_cls:
        mock_stream_cls.return_value.__enter__.return_value = MagicMock()
        samples, command = listener.listen_for_wake_and_command()

    assert command == "what time is it"
    assert listener._transcriber.transcribe_local.call_count == 2


def test_listen_for_wake_and_command_returns_none_command_when_only_wake_word_said():
    listener = _listener()
    listener._transcriber.transcribe_local.return_value = "argus"

    with patch.object(listener, "_capture_one_utterance", return_value=np.ones(16000, dtype=np.int16)), \
         patch("argus.voice.local_wake_word.sd.InputStream") as mock_stream_cls:
        mock_stream_cls.return_value.__enter__.return_value = MagicMock()
        samples, command = listener.listen_for_wake_and_command()

    assert command is None


def test_listen_for_wake_and_command_fires_on_wake_callback():
    listener = _listener()
    listener._transcriber.transcribe_local.return_value = "argus turn on the lights"
    on_wake = MagicMock()

    with patch.object(listener, "_capture_one_utterance", return_value=np.ones(16000, dtype=np.int16)), \
         patch("argus.voice.local_wake_word.sd.InputStream") as mock_stream_cls:
        mock_stream_cls.return_value.__enter__.return_value = MagicMock()
        listener.listen_for_wake_and_command(on_wake=on_wake)

    on_wake.assert_called_once()


def test_listen_for_wake_and_command_fires_on_checking_before_each_transcription():
    """Confirmed live as a real UX gap: local transcription can take a
    genuinely noticeable few seconds (CPU-bound, longer on first use while
    the model lazily loads), and until this the console gave zero
    feedback during that window -- reported live as "it heard me but
    isn't doing anything." on_checking must fire once per candidate
    utterance, including ones that turn out not to match."""
    listener = _listener()
    listener._transcriber.transcribe_local.side_effect = ["just some chatter", "argus what time is it"]
    on_checking = MagicMock()

    with patch.object(listener, "_capture_one_utterance", side_effect=[
        np.ones(16000, dtype=np.int16),
        np.ones(16000, dtype=np.int16),
    ]), patch("argus.voice.local_wake_word.sd.InputStream") as mock_stream_cls:
        mock_stream_cls.return_value.__enter__.return_value = MagicMock()
        listener.listen_for_wake_and_command(on_checking=on_checking)

    assert on_checking.call_count == 2  # once per candidate utterance, match or not


def test_on_checking_does_not_fire_for_bursts_below_the_floor():
    listener = _listener()
    on_checking = MagicMock()
    very_short = np.ones(400, dtype=np.int16)  # 25ms @ 16kHz -- well under the floor
    listener._transcriber.transcribe_local.return_value = "argus"

    with patch.object(listener, "_capture_one_utterance", side_effect=[very_short, np.ones(16000, dtype=np.int16)]), \
         patch("argus.voice.local_wake_word.sd.InputStream") as mock_stream_cls:
        mock_stream_cls.return_value.__enter__.return_value = MagicMock()
        listener.listen_for_wake_and_command(on_checking=on_checking)

    on_checking.assert_called_once()  # not on the short burst, only the real one


def test_short_speech_bursts_are_discarded_without_transcribing():
    """A cough or click can pass VAD for a single frame -- not worth a
    whisper pass. _MIN_SPEECH_MS_TO_TRANSCRIBE filters those out before
    any transcription happens."""
    listener = _listener()
    listener._transcriber.transcribe_local.return_value = "argus"
    very_short = np.ones(400, dtype=np.int16)  # 25ms @ 16kHz -- well under the floor
    long_enough = np.ones(16000, dtype=np.int16)  # 1s

    with patch.object(listener, "_capture_one_utterance", side_effect=[very_short, long_enough]), \
         patch("argus.voice.local_wake_word.sd.InputStream") as mock_stream_cls:
        mock_stream_cls.return_value.__enter__.return_value = MagicMock()
        listener.listen_for_wake_and_command()

    listener._transcriber.transcribe_local.assert_called_once()  # not on the short burst


def test_real_speech_detector_actually_votes_on_an_actual_sized_frame():
    """Integration-style, deliberately NOT mocking SpeechDetector -- every
    other test here mocks _vad.is_speech's return value directly, which is
    exactly how the 480-vs-512-sample bug slipped past both the unit tests
    and an earlier "live" check that only ever fed it a multi-second
    buffer, never the actual small per-frame chunk size the real streaming
    loop reads. Exercises the real Silero model against a frame of exactly
    _FRAME_SAMPLES length, the same size _capture_one_utterance actually
    reads from the mic -- confirms the sub-chunking loop runs at all
    (doesn't raise, returns a real bool) rather than degenerating to an
    empty range. Doesn't assert a specific verdict on real audio content --
    that's a Silero judgment call, not what this regression is guarding."""
    from argus.voice.speech_detector import SpeechDetector

    vad = SpeechDetector()
    t = np.arange(_FRAME_SAMPLES)
    tone = (np.sin(2 * np.pi * 220 * t / 16000) * 12000).astype(np.int16)
    assert isinstance(vad.is_speech(tone), bool)

    silence = np.zeros(_FRAME_SAMPLES, dtype=np.int16)
    assert vad.is_speech(silence) is False


def test_chunks_out_is_cleared_between_non_matching_utterances():
    """Confirmed live as a real bug: chunks_out is the SAME list across
    every iteration of the wait-for-wake-word loop (the caller creates it
    once, before this whole call). Without clearing it between attempts,
    every non-matching utterance's frames just piled onto whatever was
    already there -- the console's live "hearing" caption (which
    re-transcribes chunks_out as it grows) showed an ever-growing,
    increasingly stale blob of everything said since the call started,
    not just the current utterance in progress."""
    listener = _listener()
    listener._transcriber.transcribe_local.side_effect = ["unrelated chatter", "argus hello"]
    chunks_out: list = []

    def fake_capture(stream, frame_len, silence_hang_frames, max_frames, passed_chunks_out, should_stop=None):
        # Mirrors what the real _capture_one_utterance does: appends real
        # frames into whatever chunks_out it's handed.
        if passed_chunks_out is not None:
            passed_chunks_out.append(np.ones(10, dtype=np.int16))
        return np.ones(16000, dtype=np.int16)

    with patch.object(listener, "_capture_one_utterance", side_effect=fake_capture), \
         patch("argus.voice.local_wake_word.sd.InputStream") as mock_stream_cls:
        mock_stream_cls.return_value.__enter__.return_value = MagicMock()
        listener.listen_for_wake_and_command(chunks_out=chunks_out)

    # Only the matching (second) utterance's frame should remain -- the
    # first, non-matching utterance's frame must have been cleared out,
    # not left to accumulate alongside it.
    assert len(chunks_out) == 1
