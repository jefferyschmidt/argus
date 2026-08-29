import numpy as np
import torch
from silero_vad import load_silero_vad

_CHUNK_SAMPLES = 512  # Silero's required chunk size at 16kHz
# ~128ms of Silero-flagged speech (4 * 32ms sub-chunks). See is_speech()
# for why this is an ABSOLUTE floor, not a proportion of the clip.
_MIN_SPEECH_VOTES = 4


class SpeechDetector:
    """Wraps Silero's neural VAD to answer "is this actual human speech"
    rather than just "is this loud" -- RMS-threshold barge-in was firing on
    any loud transient (a cough, a click, speaker/echo bleed), since
    amplitude alone can't tell speech from noise. Previously used
    webrtcvad (a classic DSP-based classifier); switched to Silero after
    noticing the AI-receptionist project (Pipecat + Silero) handles
    turn-taking noticeably better -- Silero's small neural model is more
    accurate at telling speech from noise/echo, and still cheap enough
    (~0.5ms/chunk measured on this CPU-only hardware) to run on every
    frame with no real cost.

    threshold: Silero's own speech-probability cutoff, 0-1. 0.5 is its
    documented default."""

    def __init__(self, threshold: float = 0.5):
        self._model = load_silero_vad(onnx=True)
        self._threshold = threshold

    def reset(self) -> None:
        """Silero's model carries streaming state (recurrent) across
        calls -- reset it at the start of each new watch session so stale
        context from a previous utterance doesn't bias the next one."""
        self._model.reset_states()

    def is_speech(self, frame: np.ndarray, sample_rate: int = 16000) -> bool:
        """frame: int16 mono samples, any length, at 16kHz (the only rate
        this is wired for -- matches every other place in this codebase
        that talks to the wake-word/barge-in mic). A longer frame (e.g.
        the 80ms/1280-sample chunks used elsewhere here) is voted on in
        Silero's required 512-sample (32ms) sub-chunks -- majority
        speech-flagged wins. Returns False for a frame too short to
        contain even one full sub-chunk."""
        if sample_rate != 16000:
            raise ValueError("SpeechDetector is only wired for 16kHz audio")
        floats = frame.astype(np.float32) / 32768.0
        votes = 0
        total = 0
        for i in range(0, len(floats) - _CHUNK_SAMPLES + 1, _CHUNK_SAMPLES):
            sub = torch.from_numpy(floats[i : i + _CHUNK_SAMPLES])
            total += 1
            if self._model(sub, sample_rate).item() >= self._threshold:
                votes += 1
        if total == 0:
            return False
        # Confirmed live as a real, recurring cause of "he doesn't
        # understand half of what I say": this used to require a MAJORITY
        # of 32ms sub-chunks to be speech-flagged. But every whole-clip
        # caller (loop.py, over record_followup's output) has a fixed
        # ~900ms of trailing silence baked in by design (the silence-hang
        # that lets recording end), plus normal mid-utterance pauses -- so
        # a short, genuine utterance ("what time is it") can easily have
        # well under half its frames speech-flagged even though it's
        # unambiguously real speech. An absolute floor -- did Silero flag
        # a meaningful run of frames as speech at all -- isn't skewed by
        # clip length or padding.
        #
        # But a FIXED absolute floor broke a second, real caller:
        # local_wake_word.py's passive listener calls this once per single
        # 32ms frame (total == 1) to decide moment-to-moment whether
        # speech has started -- with total always 1 there, a fixed floor
        # of 4 can never be reached, so wake-word detection silently died
        # completely (confirmed live: "doesn't pick me up at all," push-
        # to-talk -- a separate, RMS-only path -- still worked). Capping
        # the floor at whatever this call's total actually is restores
        # the old single-chunk behavior (needs just that one chunk) while
        # keeping the real fix for long, padded clips.
        return votes >= min(_MIN_SPEECH_VOTES, total)
