import numpy as np
import sounddevice as sd

from argus.config import settings

_CHUNK_SAMPLES = 1280  # openWakeWord expects 80ms chunks @ 16kHz


class WakeWordListener:
    """Blocks until the wake word is heard. Uses openWakeWord's pretrained
    hey_jarvis_v0.1 model by default -- there's no bundled "Argus" model, so
    this is a placeholder until a custom model is trained (openWakeWord
    supports training one from synthetic TTS samples; not done yet)."""

    def __init__(self):
        import openwakeword
        from openwakeword.model import Model

        openwakeword.utils.download_models()
        self._model = Model(wakeword_models=[settings.wake_word_model])

    def wait_for_wake(self) -> None:
        self._model.reset()
        with sd.InputStream(
            samplerate=16000, channels=1, dtype="int16", blocksize=_CHUNK_SAMPLES
        ) as stream:
            while True:
                frame, _ = stream.read(_CHUNK_SAMPLES)
                scores = self._model.predict(frame.reshape(-1))
                if any(score > settings.wake_word_threshold for score in scores.values()):
                    return

    def score_frame(self, frame: np.ndarray) -> float:
        """Used by the voice loop during TTS playback to detect barge-in."""
        scores = self._model.predict(frame.reshape(-1))
        return max(scores.values()) if scores else 0.0
