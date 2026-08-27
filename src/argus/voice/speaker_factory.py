import logging

from argus.config import settings

log = logging.getLogger(__name__)


class FallbackSpeaker:
    """Tries Cartesia first (natural cloud voice); falls back to Piper
    (local, free, more robotic) if Cartesia isn't configured or a call
    fails for any reason -- network down, API error, rate limit, etc.
    Failures are per-call, not just startup-time, so a mid-session outage
    degrades gracefully instead of crashing voice mode."""

    def __init__(self):
        self._piper = None  # lazy -- only load Piper's model if actually needed
        self._cartesia = None
        if settings.cartesia_api_key:
            try:
                from argus.voice.tts_cartesia import CartesiaSpeaker
                self._cartesia = CartesiaSpeaker()
            except Exception:
                log.exception("Failed to initialize Cartesia; will use Piper only")

    def _get_piper(self):
        if self._piper is None:
            from argus.voice.tts import Speaker
            self._piper = Speaker()
        return self._piper

    def synthesize(self, text: str):
        if self._cartesia is not None:
            try:
                return self._cartesia.synthesize(text)
            except Exception:
                log.exception("Cartesia synthesis failed, falling back to Piper for this reply")
        return self._get_piper().synthesize(text)

    def speak(self, text: str, stop_event=None) -> None:
        from argus.voice.audio_io import play_audio

        synthesized = self.synthesize(text)
        if synthesized is None:
            return
        samples, sample_rate = synthesized
        play_audio(samples, sample_rate=sample_rate, stop_event=stop_event)


def build_speaker() -> FallbackSpeaker:
    return FallbackSpeaker()
