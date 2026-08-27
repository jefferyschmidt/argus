import numpy as np

from argus.config import settings

_SAMPLE_RATE = 24000


class CartesiaSpeaker:
    """Cloud TTS via Cartesia -- much more natural than Piper, but needs an
    API key + internet. Same synthesize()/speak() interface as the local
    Speaker so callers don't need to care which backend is active."""

    def __init__(self):
        from cartesia import Cartesia

        self._client = Cartesia(api_key=settings.cartesia_api_key)

    def synthesize(self, text: str) -> tuple[np.ndarray, int] | None:
        from argus.voice.text_cleanup import clean_for_speech

        text = clean_for_speech(text)
        if not text.strip():
            return None

        response = self._client.tts.generate(
            model_id=settings.cartesia_model,
            transcript=text,
            voice={"id": settings.cartesia_voice_id},
            output_format={"container": "raw", "encoding": "pcm_s16le", "sample_rate": _SAMPLE_RATE},
        )
        audio_bytes = response.read()
        samples = np.frombuffer(audio_bytes, dtype=np.int16)
        return samples, _SAMPLE_RATE

    def speak(self, text: str, stop_event=None) -> None:
        from argus.voice.audio_io import play_audio

        synthesized = self.synthesize(text)
        if synthesized is None:
            return
        samples, sample_rate = synthesized
        play_audio(samples, sample_rate=sample_rate, stop_event=stop_event)
