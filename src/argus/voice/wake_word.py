import numpy as np
import sounddevice as sd

from argus.config import settings

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
        self._model = Model(wakeword_models=[settings.wake_word_model])

    def wait_for_wake(self) -> None:
        """Standalone wake-word wait, kept for tests/tools that only need
        this. voice/loop.py uses listen_for_wake_and_command() instead so
        there's no stream-reopen gap between wake detection and recording."""
        self._model.reset()
        with sd.InputStream(
            samplerate=16000, channels=1, dtype="int16", blocksize=_CHUNK_SAMPLES
        ) as stream:
            while True:
                frame, _ = stream.read(_CHUNK_SAMPLES)
                scores = self._model.predict(frame.reshape(-1))
                if any(score > settings.wake_word_threshold for score in scores.values()):
                    return

    def listen_for_wake_and_command(self, on_wake=None) -> np.ndarray:
        """Blocks for the wake word, then records the command that follows
        on the SAME audio stream (no reopen gap, so nothing spoken right
        after the wake word gets clipped). Returns int16 mono samples."""
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
                frame, _ = stream.read(_CHUNK_SAMPLES)
                frame = frame.reshape(-1)
                chunks.append(frame)

                rms = float(np.sqrt(np.mean(frame.astype(np.float64) ** 2)))
                if rms > settings.voice_silence_rms_threshold:
                    heard_speech = True
                    silence_run = 0
                elif heard_speech:
                    silence_run += 1
                    if silence_run >= _SILENCE_HANG_CHUNKS:
                        break

        return np.concatenate(chunks) if chunks else np.array([], dtype=np.int16)

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
