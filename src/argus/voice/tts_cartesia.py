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

    def synthesize_with_visemes(self, text: str) -> tuple[np.ndarray, int, list[dict]] | None:
        """Same audio as synthesize(), plus a phoneme-timing-derived viseme
        timeline for real mouth-shape animation instead of amplitude-only.
        Uses the SSE endpoint (generate() doesn't support timestamps)."""
        from argus.voice.text_cleanup import clean_for_speech
        from argus.voice.visemes import build_viseme_timeline

        text = clean_for_speech(text)
        if not text.strip():
            return None

        stream = self._client.tts.generate_sse(
            model_id=settings.cartesia_model,
            transcript=text,
            voice={"id": settings.cartesia_voice_id},
            output_format={"container": "raw", "encoding": "pcm_s16le", "sample_rate": _SAMPLE_RATE},
            add_phoneme_timestamps=True,
        )
        audio_chunks = []
        phonemes: list[str] = []
        starts: list[float] = []
        ends: list[float] = []
        try:
            for event in stream:
                if event.type == "chunk":
                    audio = event.audio
                    if audio:
                        audio_chunks.append(audio)
                elif event.type == "phoneme_timestamps":
                    pt = event.phoneme_timestamps
                    phonemes.extend(pt.phonemes)
                    starts.extend(pt.start)
                    ends.extend(pt.end)
                elif event.type in ("done", "error"):
                    break
        finally:
            stream.close()  # unclosed SSE stream segfaults at interpreter exit

        if not audio_chunks:
            return None
        samples = np.frombuffer(b"".join(audio_chunks), dtype=np.int16)
        timeline = build_viseme_timeline(phonemes, starts, ends)
        return samples, _SAMPLE_RATE, timeline

    def speak(self, text: str, stop_event=None) -> None:
        from argus.voice.audio_io import play_audio

        synthesized = self.synthesize(text)
        if synthesized is None:
            return
        samples, sample_rate = synthesized
        play_audio(samples, sample_rate=sample_rate, stop_event=stop_event)
