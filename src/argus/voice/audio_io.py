import numpy as np
import sounddevice as sd

from argus.config import settings

FRAME_MS = 30
_SILENCE_HANG_MS = 900
_MAX_RECORD_SECONDS = 20


def record_until_silence() -> np.ndarray:
    """Records from the default mic until the user stops talking (simple
    energy-based VAD -- good enough for a quiet room, not robust to constant
    background noise). Returns int16 mono samples at settings.audio_sample_rate."""
    sr = settings.audio_sample_rate
    frame_len = int(sr * FRAME_MS / 1000)
    silence_hang_frames = _SILENCE_HANG_MS // FRAME_MS
    max_frames = (_MAX_RECORD_SECONDS * 1000) // FRAME_MS

    chunks: list[np.ndarray] = []
    silence_run = 0
    heard_speech = False

    with sd.InputStream(samplerate=sr, channels=1, dtype="int16", blocksize=frame_len) as stream:
        for _ in range(int(max_frames)):
            frame, _ = stream.read(frame_len)
            frame = frame.reshape(-1)
            chunks.append(frame)

            rms = float(np.sqrt(np.mean(frame.astype(np.float64) ** 2)))
            if rms > settings.voice_silence_rms_threshold:
                heard_speech = True
                silence_run = 0
            elif heard_speech:
                silence_run += 1
                if silence_run >= silence_hang_frames:
                    break

    return np.concatenate(chunks) if chunks else np.array([], dtype=np.int16)


def play_audio(samples: np.ndarray, sample_rate: int, stop_event=None) -> None:
    """Blocking playback. If stop_event is set mid-playback, stops immediately
    (this is the barge-in mechanism -- see voice/loop.py)."""
    sd.play(samples, samplerate=sample_rate)
    if stop_event is None:
        sd.wait()
        return
    while sd.get_stream().active:
        if stop_event.is_set():
            sd.stop()
            return
        sd.sleep(50)
