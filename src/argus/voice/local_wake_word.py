import re

import numpy as np
import sounddevice as sd

from argus.config import settings

_FRAME_SAMPLES = 512  # Silero's required chunk size at 16kHz -- confirmed live as a
# real, total bug: this used to be computed from a 30ms frame (480 samples
# at 16kHz), one sample chunk short of Silero's minimum. SpeechDetector.is_speech()
# sub-chunks its input into blocks of exactly 512; with only 480 samples the
# iteration range was empty, so it silently returned False on every single
# call, no matter what was actually said -- the wake word could never fire
# through this path at all. Using Silero's own native chunk size directly
# avoids this whole class of off-by-a-few-samples mismatch.
_FRAME_MS = _FRAME_SAMPLES * 1000 / 16000  # ~32ms, for the ms-based constants below
_SILENCE_HANG_MS = 900
_MAX_UTTERANCE_SECONDS = 20
_MIN_SPEECH_MS_TO_TRANSCRIBE = 250  # a cough/click can pass VAD for one frame; not worth a whisper pass

# "Argus" is close enough to a handful of real words/names that faster-whisper
# occasionally mishears it on short, close-mic'd clips -- confirmed by ear
# during testing. Matched as whole words (not a substring) so "Argus" inside
# a longer unrelated word never false-triggers.
_WAKE_PATTERN = re.compile(r"\b(argus|argos|arcus)\b", re.IGNORECASE)


class LocalWakeWordListener:
    """Fully local, zero-ongoing-cost alternative to the openWakeWord path
    (see WakeWordListener) -- no trained wake-word model, no cloud STT
    while idle. Silero VAD (SpeechDetector, already used for barge-in;
    measured at ~0.5ms/chunk on this hardware) runs continuously to notice
    when someone's actually talking, essentially for free. Only on a real
    speech burst does it run *local* faster-whisper (Transcriber.transcribe_local
    -- explicitly never Groq) on that clip and check the transcript for the
    wake word.

    Deliberate tradeoff vs. a trained wake-word classifier: a beat of
    latency (transcribe-then-match, not a streaming per-80ms-chunk score),
    in exchange for no training pipeline, no multi-GB downloads, and no
    ongoing API spend or continuous cloud transcription of everything said
    nearby -- picked over the trained-model path specifically because
    "no continued external API calls monitoring for the wake word" was the
    hard requirement.

    A meaningful side effect of this design (not a bolted-on feature): by
    the time the wake word is detected, the WHOLE utterance it was spoken
    in has already been captured and transcribed -- "Argus, what time is
    it" arrives as one clip, one transcription. So unlike the openWakeWord
    path (which detects the wake word mid-stream, before any command has
    been said, and then has to separately record whatever comes after),
    this can hand back the already-transcribed command text directly when
    the user says it in the same breath as the wake word, with no second
    recording phase needed at all."""

    def __init__(self):
        from argus.voice.speech_detector import SpeechDetector
        from argus.voice.stt import Transcriber

        self._vad = SpeechDetector()
        self._transcriber = Transcriber()

    def reset(self) -> None:
        self._vad.reset()

    def listen_for_wake_and_command(self, on_wake=None, chunks_out: list | None = None) -> tuple[np.ndarray, str | None]:
        """Blocks until an utterance containing the wake word is heard.
        Returns (samples, command_text): samples is the full captured
        utterance (kept for the caller's existing chunks_out/live-caption
        plumbing and as a fallback), command_text is whatever followed the
        wake word in that SAME utterance, already transcribed -- None if
        the user said only the wake word (or nothing usable followed),
        in which case the caller should fall back to recording a separate
        follow-up the normal way, exactly as it already does when
        openWakeWord is the engine."""
        sr = settings.audio_sample_rate
        frame_len = _FRAME_SAMPLES
        silence_hang_frames = int(_SILENCE_HANG_MS // _FRAME_MS)
        max_frames = int((_MAX_UTTERANCE_SECONDS * 1000) // _FRAME_MS)

        with sd.InputStream(samplerate=sr, channels=1, dtype="int16", blocksize=frame_len) as stream:
            while True:
                utterance = self._capture_one_utterance(stream, frame_len, silence_hang_frames, max_frames, chunks_out)
                if utterance is None or utterance.size == 0:
                    continue

                speech_ms = len(utterance) / sr * 1000
                if speech_ms < _MIN_SPEECH_MS_TO_TRANSCRIBE:
                    continue

                text = self._transcriber.transcribe_local(utterance)
                match = _WAKE_PATTERN.search(text)
                if not match:
                    continue

                if on_wake is not None:
                    on_wake()

                command = text[match.end():].strip(" ,.-")
                return utterance, (command or None)

    def _capture_one_utterance(self, stream, frame_len, silence_hang_frames, max_frames, chunks_out):
        """Waits for VAD-flagged speech to start, then records until a
        sustained silence. Returns None if the stream produced nothing
        speech-flagged within max_frames (lets the outer loop re-check
        rather than blocking forever on a stalled/disconnected device)."""
        chunks: list[np.ndarray] = []
        silence_run = 0
        heard_speech = False

        for _ in range(int(max_frames)):
            frame, _ = stream.read(frame_len)
            frame = frame.reshape(-1)
            is_speech = self._vad.is_speech(frame, sample_rate=16000)

            if is_speech:
                heard_speech = True
                silence_run = 0
                chunks.append(frame)
                if chunks_out is not None:
                    chunks_out.append(frame)
            elif heard_speech:
                chunks.append(frame)
                if chunks_out is not None:
                    chunks_out.append(frame)
                silence_run += 1
                if silence_run >= silence_hang_frames:
                    break
            # else: still waiting for speech to start -- frame discarded,
            # nothing buffered while idle (no transcription of silence).

        if not heard_speech:
            return None
        return np.concatenate(chunks) if chunks else None
