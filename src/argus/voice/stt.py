import numpy as np

from argus.config import settings


class Transcriber:
    def __init__(self):
        from faster_whisper import WhisperModel

        # CPU-only hardware: int8 compute type keeps this usably fast.
        self._model = WhisperModel(settings.whisper_model_size, device="cpu", compute_type="int8")

    def transcribe(self, samples: np.ndarray) -> str:
        if samples.size == 0:
            return ""
        audio = samples.astype(np.float32) / 32768.0
        segments, _ = self._model.transcribe(audio, language="en")
        return " ".join(seg.text.strip() for seg in segments).strip()
