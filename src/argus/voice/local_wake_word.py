import difflib
import re

import numpy as np
import sounddevice as sd

from argus.config import settings
from argus.voice.audio_io import ListeningPaused

_FRAME_SAMPLES = 512  # Silero's required chunk size at 16kHz -- confirmed live as a
# real, total bug: this used to be computed from a 30ms frame (480 samples
# at 16kHz), one sample chunk short of Silero's minimum. SpeechDetector.is_speech()
# sub-chunks its input into blocks of exactly 512; with only 480 samples the
# iteration range was empty, so it silently returned False on every single
# call, no matter what was actually said -- the wake word could never fire
# through this path at all. Using Silero's own native chunk size directly
# avoids this whole class of off-by-a-few-samples mismatch.
_FRAME_MS = _FRAME_SAMPLES * 1000 / 16000  # ~32ms, for the ms-based constants below
_SILENCE_HANG_MS = 900
_MAX_UTTERANCE_SECONDS = 20
_MIN_SPEECH_MS_TO_TRANSCRIBE = 250  # a cough/click can pass VAD for one frame; not worth a whisper pass

# "Argus" is close enough to a handful of real words/names that faster-whisper
# occasionally mishears it on short, close-mic'd clips -- confirmed by ear
# during testing. Matched as whole words (not a substring) so "Argus" inside
# a longer unrelated word never false-triggers.
_WAKE_PATTERN = re.compile(r"\b(argus|argos|arcus)\b", re.IGNORECASE)
_WORD_PATTERN = re.compile(r"[A-Za-z']+")
# Reported live as a real stuck-listening bug: the exact-match regex above
# is strict, and local Whisper (already documented above as prone to
# mishearing "Argus" on short clips) sometimes transcribes something close
# but not in that word list -- the utterance then just silently failed to
# match forever, looping through re-checks with no way out, which reads as
# "stuck on listening" even though speech WAS being heard and transcribed.
# This fuzzy fallback catches near-misses the strict list doesn't cover.
_FUZZY_MATCH_THRESHOLD = 0.72


def _find_wake_word(text: str) -> int | None:
    """Returns the character index right after the wake word if found
    (exact match first, then a fuzzy near-miss), else None."""
    match = _WAKE_PATTERN.search(text)
    if match:
        return match.end()
    for word_match in _WORD_PATTERN.finditer(text):
        word = word_match.group(0).lower()
        if len(word) < 4:
            continue
        if difflib.SequenceMatcher(None, word, "argus").ratio() >= _FUZZY_MATCH_THRESHOLD:
            return word_match.end()
    return None


class LocalWakeWordListener:
    """Fully local, zero-ongoing-cost alternative to the openWakeWord path
    (see WakeWordListener) -- no trained wake-word model, no cloud STT
    while idle. Silero VAD (SpeechDetector, already used for barge-in;
    measured at ~0.5ms/chunk on this hardware) runs continuously to notice
    when someone's actually talking, essentially for free. Only on a real
    speech burst does it run *local* faster-whisper (Transcriber.transcribe_local
    -- explicitly never Groq) on that clip and check the transcript for the
    wake word.

    Deliberate tradeoff vs. a trained wake-word classifier: a beat of
    latency (transcribe-then-match, not a streaming per-80ms-chunk score),
    in exchange for no training pipeline, no multi-GB downloads, and no
    ongoing API spend or continuous cloud transcription of everything said
    nearby -- picked over the trained-model path specifically because
    "no continued external API calls monitoring for the wake word" was the
    hard requirement.

    A meaningful side effect of this design (not a bolted-on feature): by
    the time the wake word is detected, the WHOLE utterance it was spoken
    in has already been captured and transcribed -- "Argus, what time is
    it" arrives as one clip, one transcription. So unlike the openWakeWord
    path (which detects the wake word mid-stream, before any command has
    been said, and then has to separately record whatever comes after),
    this can hand back the already-transcribed command text directly when
    the user says it in the same breath as the wake word, with no second
    recording phase needed at all."""

    def __init__(self):
        from argus.voice.speech_detector import SpeechDetector
        from argus.voice.stt import Transcriber

        self._vad = SpeechDetector()
        self._transcriber = Transcriber()

    def reset(self) -> None:
        self._vad.reset()

    def listen_for_wake_and_command(
        self, on_wake=None, chunks_out: list | None = None, on_checking=None, hot_mic_check=None,
        should_stop=None, via_hot_mic_out: list | None = None,
    ) -> tuple[np.ndarray, str | None]:
        """Blocks until an utterance containing the wake word is heard.
        Returns (samples, command_text): samples is the full captured
        utterance (kept for the caller's existing chunks_out/live-caption
        plumbing and as a fallback), command_text is whatever followed the
        wake word in that SAME utterance, already transcribed -- None if
        the user said only the wake word (or nothing usable followed),
        in which case the caller should fall back to recording a separate
        follow-up the normal way, exactly as it already does when
        openWakeWord is the engine.

        on_checking, if given, fires right before each candidate utterance
        is transcribed to check for the wake word -- confirmed live as a
        real UX gap: local (CPU-bound) transcription can take a genuinely
        noticeable few seconds, especially the first time it runs in a
        process (the model loads lazily on first use), and until this the
        console gave zero feedback during that window -- it just kept
        showing "waiting for the wake word" the whole time, even though
        real speech had already been captured and was actively being
        checked. Reported live as "it heard me but isn't doing anything."

        hot_mic_check, if given, is called once per candidate utterance;
        when it returns True, that utterance is treated as addressed to
        Argus without requiring the wake word at all. Confirmed live as a
        real gap: anything Argus said on its own initiative (a proactive
        check-in, an email alert) never opened a hands-free follow-up
        window the way a normal reply did, so answering it directly with
        no wake word looked like Argus silently ignoring what was said.

        via_hot_mic_out, if given, gets True appended to it when a return
        happened via the hot_mic_check path above rather than a genuine
        wake-word match. Confirmed live as a real, related bug: the caller
        used to treat EVERY return from this method as equally explicit
        intent (skipping the addressee gate) -- fine for a real wake word,
        wrong for a hot-mic-window capture, which is exactly as likely to
        be background noise/another conversation as a normal follow-up
        utterance is. Caused a real incident: background video audio
        during an open hot-mic window got treated as a direct question,
        Argus replied to it, and a loud line from the SAME video then
        triggered a false barge-in mid-reply, cutting Argus off and (once
        the resulting "was that a real interruption?" check correctly
        decided no) resuming and re-speaking most of the reply -- read
        live as "he repeated the whole thing." The caller now checks this
        to decide whether to still run the addressee gate.

        should_stop, if given, is checked once per audio frame (not just
        once per utterance) and raises ListeningPaused immediately when it
        returns True -- confirmed live as a real gap: "Stop listening"
        previously only took effect between utterance-capture attempts, so
        the mic kept actively capturing (and transcribing) for however
        long whatever was already in progress took. See ListeningPaused's
        docstring for why this has to be a raise, not an early return."""
        sr = settings.audio_sample_rate
        frame_len = _FRAME_SAMPLES
        silence_hang_frames = int(_SILENCE_HANG_MS // _FRAME_MS)
        max_frames = int((_MAX_UTTERANCE_SECONDS * 1000) // _FRAME_MS)

        with sd.InputStream(samplerate=sr, channels=1, dtype="int16", blocksize=frame_len) as stream:
            while True:
                if should_stop is not None and should_stop():
                    raise ListeningPaused()
                # chunks_out is the SAME list across every iteration of this
                # loop (the caller creates it once, before this whole call)
                # -- confirmed live as a real bug: without clearing it here,
                # every non-matching utterance's frames just kept piling
                # onto whatever was already there, so the console's live
                # "hearing" caption (which re-transcribes chunks_out as it
                # grows -- see _start_hearing_watcher) accumulated an
                # ever-growing, increasingly stale blob of everything said
                # since this call started, minutes of unrelated speech
                # concatenated together, instead of just showing the
                # current utterance in progress. Wake-word matching itself
                # was never affected (that uses _capture_one_utterance's
                # own fresh local buffer each call) -- this was purely
                # about what the live caption displayed.
                if chunks_out is not None:
                    chunks_out.clear()
                utterance = self._capture_one_utterance(
                    stream, frame_len, silence_hang_frames, max_frames, chunks_out, should_stop
                )
                if utterance is None or utterance.size == 0:
                    continue

                speech_ms = len(utterance) / sr * 1000
                if speech_ms < _MIN_SPEECH_MS_TO_TRANSCRIBE:
                    continue

                if on_checking is not None:
                    on_checking()
                text = self._transcriber.transcribe_local(utterance)

                if hot_mic_check is not None and hot_mic_check():
                    if on_wake is not None:
                        on_wake()
                    if via_hot_mic_out is not None:
                        via_hot_mic_out.append(True)
                    return utterance, (text.strip() or None)

                end_idx = _find_wake_word(text)
                if end_idx is None:
                    continue

                if on_wake is not None:
                    on_wake()

                command = text[end_idx:].strip(" ,.-")
                return utterance, (command or None)

    def _capture_one_utterance(self, stream, frame_len, silence_hang_frames, max_frames, chunks_out, should_stop=None):
        """Waits for VAD-flagged speech to start, then records until a
        sustained silence. Returns None if the stream produced nothing
        speech-flagged within max_frames (lets the outer loop re-check
        rather than blocking forever on a stalled/disconnected device).

        should_stop: see listen_for_wake_and_command's docstring -- checked
        every frame here too, not just once per utterance, since this is
        where most of the actual blocking time happens."""
        chunks: list[np.ndarray] = []
        silence_run = 0
        heard_speech = False

        for _ in range(int(max_frames)):
            if should_stop is not None and should_stop():
                raise ListeningPaused()
            frame, _ = stream.read(frame_len)
            frame = frame.reshape(-1)
            is_speech = self._vad.is_speech(frame, sample_rate=16000)

            if is_speech:
                heard_speech = True
                silence_run = 0
                chunks.append(frame)
                if chunks_out is not None:
                    chunks_out.append(frame)
            elif heard_speech:
                chunks.append(frame)
                if chunks_out is not None:
                    chunks_out.append(frame)
                silence_run += 1
                if silence_run >= silence_hang_frames:
                    break
            # else: still waiting for speech to start -- frame discarded,
            # nothing buffered while idle (no transcription of silence).

        if not heard_speech:
            return None
        return np.concatenate(chunks) if chunks else None
