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


def record_followup(timeout_seconds: float) -> np.ndarray | None:
    """Listens (no wake word needed) for up to timeout_seconds for the user
    to start talking. Returns None if nothing was said in time -- caller
    should fall back to requiring the wake word again. If speech starts,
    records until silence same as record_until_silence, with no separate
    timeout on the rest of the utterance."""
    sr = settings.audio_sample_rate
    frame_len = int(sr * FRAME_MS / 1000)
    silence_hang_frames = _SILENCE_HANG_MS // FRAME_MS
    timeout_frames = int(timeout_seconds * 1000 / FRAME_MS)
    max_frames = (_MAX_RECORD_SECONDS * 1000) // FRAME_MS

    chunks: list[np.ndarray] = []
    silence_run = 0
    heard_speech = False
    waited_frames = 0

    with sd.InputStream(samplerate=sr, channels=1, dtype="int16", blocksize=frame_len) as stream:
        for _ in range(int(max_frames)):
            frame, _ = stream.read(frame_len)
            frame = frame.reshape(-1)

            rms = float(np.sqrt(np.mean(frame.astype(np.float64) ** 2)))
            if rms > settings.voice_silence_rms_threshold:
                if not heard_speech:
                    heard_speech = True
                chunks.append(frame)
                silence_run = 0
            elif heard_speech:
                chunks.append(frame)
                silence_run += 1
                if silence_run >= silence_hang_frames:
                    break
            else:
                waited_frames += 1
                if waited_frames >= timeout_frames:
                    return None

    return np.concatenate(chunks) if chunks else None


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
