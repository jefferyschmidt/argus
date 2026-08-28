import numpy as np
import webrtcvad


class SpeechDetector:
    """Wraps WebRTC's VAD to answer "is this actual human speech" rather
    than just "is this loud" -- RMS-threshold barge-in was firing on any
    loud transient (a cough, a click, speaker/echo bleed), since amplitude
    alone can't tell speech from noise. WebRTC's classifier looks at the
    audio's spectral shape instead, and is small/fast enough to run on
    every frame on CPU-only hardware with no real cost.

    aggressiveness: 0 (most permissive, most false positives) to 3 (most
    aggressive at filtering out non-speech). 2 is WebRTC's own suggested
    default for a noisy environment."""

    def __init__(self, aggressiveness: int = 2):
        self._vad = webrtcvad.Vad(aggressiveness)

    def is_speech(self, frame: np.ndarray, sample_rate: int = 16000) -> bool:
        """frame: int16 mono samples, any length. WebRTC VAD only accepts
        10/20/30ms sub-frames at 8/16/32/48kHz, so a longer frame (e.g. the
        80ms chunks used elsewhere in this codebase) is voted on in 20ms
        slices -- majority speech-flagged wins. Returns False for a frame
        too short to contain even one full sub-frame."""
        sub_len = int(sample_rate * 0.02)  # 20ms
        votes = 0
        total = 0
        for i in range(0, len(frame) - sub_len + 1, sub_len):
            sub = frame[i : i + sub_len]
            total += 1
            if self._vad.is_speech(sub.astype(np.int16).tobytes(), sample_rate):
                votes += 1
        if total == 0:
            return False
        return votes / total >= 0.5
