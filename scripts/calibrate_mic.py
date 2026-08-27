"""Records 5s of mic input and reports RMS levels, so
settings.voice_silence_rms_threshold (in .env as VOICE_SILENCE_RMS_THRESHOLD)
can be tuned for your actual microphone -- levels vary a lot by hardware/OS
driver, and a threshold picked for one mic can silently fail on another
(this is exactly how the default 300.0 turned out to be way too high)."""

import numpy as np
import sounddevice as sd

SR = 16000
FRAME_MS = 30


def main() -> None:
    frame_len = int(SR * FRAME_MS / 1000)
    n_frames = int(5000 / FRAME_MS)
    print("Recording 5s -- talk normally partway through, then go quiet.")

    silence_rms: list[float] = []
    speech_rms: list[float] = []

    with sd.InputStream(samplerate=SR, channels=1, dtype="int16", blocksize=frame_len) as stream:
        for _ in range(n_frames):
            frame, _ = stream.read(frame_len)
            rms = float(np.sqrt(np.mean(frame.reshape(-1).astype(np.float64) ** 2)))
            (speech_rms if rms > 50 else silence_rms).append(rms)

    floor = max(silence_rms) if silence_rms else 0.0
    peak = max(speech_rms) if speech_rms else 0.0
    suggested = floor + (peak - floor) * 0.3 if peak > floor else max(floor * 3, 50)

    print(f"\nnoise floor (quiet frames): up to {floor:.1f}")
    print(f"speech peak (loud frames):  up to {peak:.1f}")
    print(f"\nSuggested VOICE_SILENCE_RMS_THRESHOLD={suggested:.0f} in .env")


if __name__ == "__main__":
    main()
