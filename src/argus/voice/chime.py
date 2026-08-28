"""Short synthesized audio cues for listening-state transitions -- confirmed
directly requested: "would make it easier to know Argus' status when I'm
not staring at the screen." Plain synthesized tones (numpy sine waves, no
asset files, no TTS call/cost) played on the same speakers Argus's own
voice already uses -- deliberately server-side rather than played from the
browser console, so the cue works regardless of whether a console tab is
even open, matching how the rest of Argus's audio behavior already works.

Fire-and-forget: never blocks the caller waiting for the cue to finish,
and never raises -- a missing/busy audio device should never break the
actual listening-state transition it's just signaling."""

import logging
import threading

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)

_SAMPLE_RATE = 22050


def _tone(freq: float, duration_s: float, amplitude: float = 0.18) -> np.ndarray:
    t = np.linspace(0, duration_s, int(_SAMPLE_RATE * duration_s), endpoint=False)
    # A short fade in/out on every tone -- a hard-edged sine click/pops
    # audibly on cheap speakers, an envelope avoids that.
    envelope = np.ones_like(t)
    fade_n = max(1, int(_SAMPLE_RATE * 0.01))
    envelope[:fade_n] = np.linspace(0, 1, fade_n)
    envelope[-fade_n:] = np.linspace(1, 0, fade_n)
    return (np.sin(2 * np.pi * freq * t) * envelope * amplitude).astype(np.float32)


# Rising two-tone chime for "now actively listening," falling for "no
# longer listening" -- the same up/down convention most notification
# sounds already use, so it reads as intuitive without needing to be learned.
_LISTENING_CHIME = np.concatenate([_tone(660, 0.07), np.zeros(int(_SAMPLE_RATE * 0.02), dtype=np.float32), _tone(880, 0.09)])
_STOPPED_CHIME = np.concatenate([_tone(660, 0.07), np.zeros(int(_SAMPLE_RATE * 0.02), dtype=np.float32), _tone(440, 0.09)])


def _play(samples: np.ndarray) -> None:
    def _worker() -> None:
        try:
            sd.play(samples, samplerate=_SAMPLE_RATE)
            sd.wait()
        except Exception:
            log.exception("Failed to play a listening-state chime")

    threading.Thread(target=_worker, daemon=True).start()


def play_listening_chime() -> None:
    _play(_LISTENING_CHIME)


def play_stopped_chime() -> None:
    _play(_STOPPED_CHIME)
