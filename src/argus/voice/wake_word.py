import numpy as np
import sounddevice as sd

from argus.config import settings
from argus.voice.audio_io import ListeningPaused

_CHUNK_SAMPLES = 1280  # openWakeWord expects 80ms chunks @ 16kHz
_SILENCE_HANG_CHUNKS = 11  # ~880ms at 80ms/chunk
_MAX_COMMAND_CHUNKS = 250  # ~20s cap


class WakeWordListener:
    """Uses openWakeWord's pretrained hey_jarvis_v0.1 model by default --
    there's no bundled "Argus" model, so this is a placeholder until a
    custom model is trained (openWakeWord supports training one from
    synthetic TTS samples; not done yet)."""

    def __init__(self):
        import openwakeword
        from openwakeword.model import Model

        openwakeword.utils.download_models()
        self._model = Model(wakeword_models=[settings.openwakeword_model_name])

    def listen_for_wake_and_command(
        self, on_wake=None, chunks_out: list | None = None, on_checking=None, hot_mic_check=None,
        should_stop=None, via_hot_mic_out: list | None = None,
    ) -> tuple[np.ndarray, str | None]:
        """Blocks for the wake word, then records the command that follows
        on the SAME audio stream (no reopen gap, so nothing spoken right
        after the wake word gets clipped). Returns (samples, None) -- int16
        mono samples plus a command-text slot that's always None here
        (openWakeWord only ever detects the wake word itself, never
        transcribes anything, so it has no pre-transcribed text to offer;
        the None just keeps this engine's return shape interchangeable
        with LocalWakeWordListener's, which sometimes does).

        chunks_out: optional list that command-phase frames are appended
        into as they're captured, so a caller running on another thread can
        read the in-progress audio for live captioning.

        on_checking: accepted for interface parity with LocalWakeWordListener
        but never called here -- openWakeWord scores every frame directly
        (no separate slow transcription step to signal), so there's no
        "checking" phase distinct from normal listening.

        hot_mic_check: accepted for interface parity but never called here
        -- this engine has no transcript to fall back on if it skipped the
        wake-word check (see listen_for_wake_and_command's docstring on
        LocalWakeWordListener), so honoring it would require transcribing
        every utterance anyway, defeating the point of this engine.

        should_stop: checked once per frame in both phases, raises
        ListeningPaused immediately when it returns True -- same real-mute
        behavior as LocalWakeWordListener, see ListeningPaused's docstring.

        via_hot_mic_out: accepted for interface parity but never appended
        to here, since hot_mic_check is never honored by this engine
        either (see above)."""
        from collections import deque

        self._model.reset()
        silence_run = 0
        heard_speech = False
        # openWakeWord's detection score lags slightly behind the actual
        # wake phrase (it scores over a rolling window), so the trigger
        # frame arrives a beat after you finish saying it. Keep the last few
        # frames so that trailing bit of speech isn't lost.
        recent = deque(maxlen=3)

        with sd.InputStream(
            samplerate=16000, channels=1, dtype="int16", blocksize=_CHUNK_SAMPLES
        ) as stream:
            # Phase 1: wait for wake word.
            while True:
                if should_stop is not None and should_stop():
                    raise ListeningPaused()
                frame, _ = stream.read(_CHUNK_SAMPLES)
                frame = frame.reshape(-1)
                recent.append(frame)
                scores = self._model.predict(frame)
                if any(score > settings.wake_word_threshold for score in scores.values()):
                    break

            if on_wake is not None:
                on_wake()
            chunks: list[np.ndarray] = list(recent)

            # Phase 2: record the command on the same stream, starting
            # immediately -- no stream reopen, no dropped words.
            for _ in range(_MAX_COMMAND_CHUNKS):
                if should_stop is not None and should_stop():
                    raise ListeningPaused()
                frame, _ = stream.read(_CHUNK_SAMPLES)
                frame = frame.reshape(-1)
                chunks.append(frame)
                if chunks_out is not None:
                    chunks_out.append(frame)

                rms = float(np.sqrt(np.mean(frame.astype(np.float64) ** 2)))
                if rms > settings.voice_silence_rms_threshold:
                    heard_speech = True
                    silence_run = 0
                elif heard_speech:
                    silence_run += 1
                    if silence_run >= _SILENCE_HANG_CHUNKS:
                        break

        samples = np.concatenate(chunks) if chunks else np.array([], dtype=np.int16)
        return samples, None

    def reset(self) -> None:
        """Clears the model's internal rolling-prediction state. Call this
        before starting a fresh listening session on a model instance that
        was already used -- without it, residual state from the previous
        detection (patience/debounce history) can cause spurious immediate
        triggers on the very next predict() calls."""
        self._model.reset()

    def score_frame(self, frame: np.ndarray) -> float:
        """Used by the voice loop during TTS playback to detect barge-in."""
        scores = self._model.predict(frame.reshape(-1))
        return max(scores.values()) if scores else 0.0
