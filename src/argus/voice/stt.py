import io
import logging
import wave

import numpy as np

from argus.config import settings

log = logging.getLogger(__name__)


def _to_wav_bytes(samples: np.ndarray, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # int16
        wf.setframerate(sample_rate)
        wf.writeframes(samples.tobytes())
    return buf.getvalue()


class Transcriber:
    """Groq's hosted Whisper when configured -- same motivation as
    GroqClient replacing Ollama: local faster-whisper is CPU-bound on this
    hardware and STT runs on every utterance, including the periodic
    re-transcription behind the live "hearing" captions, so it's an even
    hotter path than chat completion. Falls back to local faster-whisper
    (loaded lazily, only if actually needed) both when Groq isn't
    configured and if a Groq call fails at runtime -- STT is on the
    critical path for every single interaction, so a transient network
    blip shouldn't silently mean Argus stops understanding speech."""

    def __init__(self):
        self._local_model = None
        self._groq = None
        if settings.groq_api_key:
            from groq import Groq

            self._groq = Groq(api_key=settings.groq_api_key)

    def _local(self):
        if self._local_model is None:
            from faster_whisper import WhisperModel

            # CPU-only hardware: int8 compute type keeps this usably fast.
            self._local_model = WhisperModel(settings.whisper_model_size, device="cpu", compute_type="int8")
        return self._local_model

    def transcribe(self, samples: np.ndarray) -> str:
        if samples.size == 0:
            return ""

        if self._groq is not None:
            try:
                return self._transcribe_groq(samples)
            except Exception:
                log.exception("Groq STT failed; falling back to local Whisper for this utterance")

        return self._transcribe_local(samples)

    def _transcribe_groq(self, samples: np.ndarray) -> str:
        wav_bytes = _to_wav_bytes(samples, settings.audio_sample_rate)
        response = self._groq.audio.transcriptions.create(
            file=("audio.wav", wav_bytes),
            model=settings.groq_whisper_model,
            language="en",
        )
        return (response.text or "").strip()

    def _transcribe_local(self, samples: np.ndarray) -> str:
        audio = samples.astype(np.float32) / 32768.0
        segments, _ = self._local().transcribe(audio, language="en")
        return " ".join(seg.text.strip() for seg in segments).strip()
