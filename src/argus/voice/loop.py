import logging
import queue
import threading
import time

from rich.console import Console

from argus.config import settings
from argus.orchestrator import Orchestrator
from argus.voice.audio_io import record_followup
from argus.voice.speaker_factory import build_speaker
from argus.voice.stt import Transcriber
from argus.voice.wake_word import WakeWordListener
from argus.ui import events as ui_events

log = logging.getLogger(__name__)
console = Console()

_STOP_LISTENING_PHRASES = ("stop listening", "stop barging in", "quit interrupting")


class _BargeInInterrupt(Exception):
    """Raised from the streaming callback to unwind out of the generation
    call early once barge-in has fired -- otherwise the model keeps
    generating (and we keep paying for it) after the user already
    interrupted."""


class VoiceLoop:
    """wake word -> record -> transcribe -> streamed orchestrator reply ->
    sentence-by-sentence TTS, with everything also printed to the screen
    (spoken output is never the only output, per the original requirement).

    Streaming: replies are spoken sentence-by-sentence as they're generated
    rather than waiting for the full response, so time-to-first-sound is
    close to one sentence's worth of generation instead of the whole reply.

    Conversational follow-ups: after Argus finishes speaking, it keeps
    listening (no wake word needed) for settings.followup_window_seconds --
    if you keep talking, the conversation continues hands-free; go quiet
    for that window and it drops back to requiring the wake word.

    Barge-in: while a sentence plays, Argus listens in the background for an
    interruption. Two tiers:
    - Hot mic (settings.open_barge_in_seconds after the wake word, refreshed
      by activity): plain volume detection -- just start talking, no wake
      word needed. Higher self-feedback risk on open speakers since it's
      not checking for anything specific, just loudness.
    - Otherwise: the wake-word model listening for "hey jarvis" again, which
      is far less prone to false-triggering on Argus's own voice since it
      needs to phonetically match.
    Say "stop listening" to end the hot-mic window early."""

    def __init__(self, orchestrator: Orchestrator | None = None):
        console.print("[dim]Loading voice models (wake word, STT, TTS)...[/dim]")
        self.orchestrator = orchestrator or Orchestrator()
        self.wake_word = WakeWordListener()
        self.transcriber = Transcriber()
        self.speaker = build_speaker()
        self._hot_mic_until = 0.0

    def _refresh_hot_mic(self) -> None:
        if settings.open_barge_in_seconds > 0:
            self._hot_mic_until = time.monotonic() + settings.open_barge_in_seconds

    def _hot_mic_active(self) -> bool:
        return time.monotonic() < self._hot_mic_until

    def run(self) -> None:
        console.print("[bold cyan]Argus[/bold cyan] listening for wake word. Ctrl+C to quit.\n")
        while True:
            ui_events.publish({"type": "state", "value": "listening"})
            try:
                def _on_wake():
                    console.print("[green](wake word heard, listening...)[/green]")
                    ui_events.publish({"type": "state", "value": "listening"})
                    self._refresh_hot_mic()

                samples = self.wake_word.listen_for_wake_and_command(on_wake=_on_wake)
            except KeyboardInterrupt:
                break

            if not self._process_utterance(samples):
                console.print("[dim](heard nothing, back to listening)[/dim]\n")
                continue

            # Conversational follow-ups: keep listening without the wake
            # word until the user goes quiet for the timeout window.
            while True:
                console.print(f"[dim](listening for follow-up, {settings.followup_window_seconds:.0f}s...)[/dim]")
                ui_events.publish({"type": "state", "value": "listening"})
                followup = record_followup(settings.followup_window_seconds)
                if followup is None or not self._process_utterance(followup):
                    console.print("[dim](back to wake-word listening)[/dim]\n")
                    break

    def _process_utterance(self, samples) -> bool:
        """Transcribes, then streams the reply through the orchestrator,
        speaking each sentence as it's generated. Returns False if nothing
        usable was heard (empty/silence)."""
        text = self.transcriber.transcribe(samples)
        if not text:
            return False

        console.print(f"[bold green]you>[/bold green] {text}")

        if any(phrase in text.lower() for phrase in _STOP_LISTENING_PHRASES):
            self._hot_mic_until = 0.0
            console.print("[dim](hot mic off -- wake word needed to interrupt again)[/dim]\n")
            return True

        self._refresh_hot_mic()

        sentence_queue: queue.Queue[str | None] = queue.Queue()
        interrupted = threading.Event()

        def consumer() -> None:
            while True:
                sentence = sentence_queue.get()
                if sentence is None:
                    return
                console.print(f"[bold cyan]argus>[/bold cyan] {sentence}")
                ui_events.publish({"type": "caption", "text": sentence})
                if self._speak_with_barge_in(sentence):
                    interrupted.set()
                    return

        consumer_thread = threading.Thread(target=consumer)
        consumer_thread.start()

        def on_sentence(sentence: str) -> None:
            if interrupted.is_set():
                raise _BargeInInterrupt()
            sentence_queue.put(sentence)

        try:
            self.orchestrator.handle_streaming(text, on_sentence=on_sentence)
        except _BargeInInterrupt:
            pass
        finally:
            sentence_queue.put(None)
            consumer_thread.join()

        if self.orchestrator.last_tier is not None:
            tag = f"[dim]({self.orchestrator.last_tier.value}: {self.orchestrator.last_model})[/dim]"
            console.print(f"{tag}\n")
        return True

    def _speak_with_barge_in(self, text: str) -> bool:
        """Synthesizes and plays one sentence. Returns True if barge-in
        interrupted it."""
        # Synthesize BEFORE starting the watcher -- Piper's synthesis is
        # itself onnxruntime compute, and letting it overlap with the
        # watcher's continuous wake-word inference was starving the CPU on
        # this hardware badly enough to produce long silences.
        try:
            synthesized = self.speaker.synthesize(text)
        except Exception:
            log.exception("Speech synthesis failed")
            console.print("[red](speech synthesis failed -- see log)[/red]")
            return False
        if synthesized is None:
            return False
        samples, sample_rate = synthesized

        # Publish state + the real amplitude envelope together, right as
        # playback is about to start -- the UI's mouth animation follows
        # this envelope instead of a fake time-based wobble.
        from argus.voice.audio_io import compute_envelope
        envelope = compute_envelope(samples, sample_rate)
        chunk_ms = 40
        ui_events.publish({
            "type": "state", "value": "speaking",
            "envelope": envelope, "chunk_ms": chunk_ms,
            "duration_ms": round(len(samples) / sample_rate * 1000),
        })

        stop_event = threading.Event()

        def _play_safely():
            from argus.voice.audio_io import play_audio
            try:
                play_audio(samples, sample_rate=sample_rate, stop_event=stop_event)
            except Exception:
                log.exception("Speech playback failed")
                console.print("[red](speech playback failed -- see log)[/red]")

        if not settings.voice_barge_in_enabled:
            _play_safely()
            return False

        play_thread = threading.Thread(target=_play_safely)
        play_thread.start()
        interrupted = False
        try:
            interrupted = self._watch_for_barge_in(stop_event, play_thread)
        except Exception:
            log.exception("Barge-in watcher failed; continuing without it")
        play_thread.join()
        if interrupted:
            self._refresh_hot_mic()
        return interrupted

    def _watch_for_barge_in(self, stop_event: threading.Event, play_thread: threading.Thread) -> bool:
        import numpy as np
        import sounddevice as sd

        hot_mic = self._hot_mic_active()

        # Without this, leftover prediction state from the wake-word model's
        # last real detection can cause an immediate spurious trigger here.
        self.wake_word.reset()

        # openWakeWord's embedding model scores over a rolling ~1.3s window.
        # Right after reset() that buffer is mostly empty, and scoring
        # against it produced sharp spurious near-1.0 triggers within the
        # first 1-2 frames in testing -- an artifact of the cold buffer, not
        # real audio. Feed the model during warm-up without acting on scores
        # (skip entirely in hot-mic mode -- it doesn't use wake-word scoring).
        warmup_chunks = 0 if hot_mic else 16  # ~1.28s at 80ms/chunk
        chunk = 1280
        with sd.InputStream(samplerate=16000, channels=1, dtype="int16", blocksize=chunk) as stream:
            for _ in range(warmup_chunks):
                if not play_thread.is_alive():
                    return False
                frame, _ = stream.read(chunk)
                self.wake_word.score_frame(frame.reshape(-1))

            while play_thread.is_alive():
                frame, _ = stream.read(chunk)
                frame = frame.reshape(-1)

                if hot_mic:
                    rms = float(np.sqrt(np.mean(frame.astype(np.float64) ** 2)))
                    if rms > settings.voice_silence_rms_threshold:
                        console.print("[yellow](barge-in: hot mic heard you)[/yellow]")
                        log.info("hot-mic barge-in triggered at rms=%.1f", rms)
                        stop_event.set()
                        return True
                    continue

                score = self.wake_word.score_frame(frame)
                if score > 0.2:
                    log.info("barge-in watch: score=%.3f", score)
                if score > 0.6:
                    console.print("[yellow](barge-in: stopping speech)[/yellow]")
                    log.info("barge-in triggered at score=%.3f", score)
                    stop_event.set()
                    return True
        return False
