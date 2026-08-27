import logging
import threading

from rich.console import Console

from argus.config import settings
from argus.orchestrator import Orchestrator
from argus.voice.audio_io import record_followup
from argus.voice.stt import Transcriber
from argus.voice.tts import Speaker
from argus.voice.wake_word import WakeWordListener

log = logging.getLogger(__name__)
console = Console()


class VoiceLoop:
    """wake word -> record -> transcribe -> orchestrator -> speak, with
    everything also printed to the screen (spoken output is never the only
    output, per the original requirement).

    Conversational follow-ups: after Argus finishes speaking, it keeps
    listening (no wake word needed) for settings.followup_window_seconds --
    if you keep talking, the conversation continues hands-free; go quiet
    for that window and it drops back to requiring the wake word.

    Barge-in: while Argus is speaking, the wake-word model keeps listening
    in the background; hearing the wake word again cuts playback and starts
    a new recording immediately. Caveat: without acoustic echo cancellation,
    speaking through open speakers (not headphones) risks the mic picking up
    Argus's own voice and false-triggering. Headphones avoid this cleanly."""

    def __init__(self, orchestrator: Orchestrator | None = None):
        console.print("[dim]Loading voice models (wake word, STT, TTS)...[/dim]")
        self.orchestrator = orchestrator or Orchestrator()
        self.wake_word = WakeWordListener()
        self.transcriber = Transcriber()
        self.speaker = Speaker()

    def run(self) -> None:
        console.print("[bold cyan]Argus[/bold cyan] listening for wake word. Ctrl+C to quit.\n")
        while True:
            try:
                samples = self.wake_word.listen_for_wake_and_command(
                    on_wake=lambda: console.print("[green](wake word heard, listening...)[/green]")
                )
            except KeyboardInterrupt:
                break

            if not self._process_utterance(samples):
                console.print("[dim](heard nothing, back to listening)[/dim]\n")
                continue

            # Conversational follow-ups: keep listening without the wake
            # word until the user goes quiet for the timeout window.
            while True:
                console.print(f"[dim](listening for follow-up, {settings.followup_window_seconds:.0f}s...)[/dim]")
                followup = record_followup(settings.followup_window_seconds)
                if followup is None or not self._process_utterance(followup):
                    console.print("[dim](back to wake-word listening)[/dim]\n")
                    break

    def _process_utterance(self, samples) -> bool:
        """Transcribes, routes through the orchestrator, and speaks the
        reply. Returns False if nothing usable was heard (empty/silence)."""
        text = self.transcriber.transcribe(samples)
        if not text:
            return False

        console.print(f"[bold green]you>[/bold green] {text}")
        reply = self.orchestrator.handle(text)
        tag = f"[dim]({self.orchestrator.last_tier.value}: {self.orchestrator.last_model})[/dim]"
        console.print(f"[bold cyan]argus>[/bold cyan] {reply} {tag}\n")

        self._speak_with_barge_in(reply)
        return True

    def _speak_with_barge_in(self, text: str) -> None:
        # Synthesize BEFORE starting the watcher -- Piper's synthesis is
        # itself onnxruntime compute, and letting it overlap with the
        # watcher's continuous wake-word inference was starving the CPU on
        # this hardware badly enough to produce long silences.
        try:
            synthesized = self.speaker.synthesize(text)
        except Exception:
            log.exception("Speech synthesis failed")
            console.print("[red](speech synthesis failed -- see log)[/red]")
            return
        if synthesized is None:
            return
        samples, sample_rate = synthesized

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
            return

        play_thread = threading.Thread(target=_play_safely)
        play_thread.start()
        try:
            self._watch_for_barge_in(stop_event, play_thread)
        except Exception:
            log.exception("Barge-in watcher failed; continuing without it")
        play_thread.join()

    def _watch_for_barge_in(self, stop_event: threading.Event, play_thread: threading.Thread) -> None:
        import sounddevice as sd

        # Without this, leftover prediction state from the wake-word model's
        # last real detection can cause an immediate spurious trigger here.
        self.wake_word.reset()

        # openWakeWord's embedding model scores over a rolling ~1.3s window.
        # Right after reset() that buffer is mostly empty, and scoring
        # against it produced sharp spurious near-1.0 triggers within the
        # first 1-2 frames in testing -- an artifact of the cold buffer, not
        # real audio. Feed the model during warm-up without acting on scores.
        warmup_chunks = 16  # ~1.28s at 80ms/chunk
        chunk = 1280
        with sd.InputStream(samplerate=16000, channels=1, dtype="int16", blocksize=chunk) as stream:
            for _ in range(warmup_chunks):
                if not play_thread.is_alive():
                    return
                frame, _ = stream.read(chunk)
                self.wake_word.score_frame(frame.reshape(-1))

            while play_thread.is_alive():
                frame, _ = stream.read(chunk)
                score = self.wake_word.score_frame(frame.reshape(-1))
                if score > 0.2:
                    log.info("barge-in watch: score=%.3f", score)
                if score > 0.6:
                    console.print("[yellow](barge-in: stopping speech)[/yellow]")
                    log.info("barge-in triggered at score=%.3f", score)
                    stop_event.set()
                    return
