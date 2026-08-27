import logging
import threading

from rich.console import Console

from argus.orchestrator import Orchestrator
from argus.voice.stt import Transcriber
from argus.voice.tts import Speaker
from argus.voice.wake_word import WakeWordListener

log = logging.getLogger(__name__)
console = Console()


class VoiceLoop:
    """wake word -> record -> transcribe -> orchestrator -> speak, with
    everything also printed to the screen (spoken output is never the only
    output, per the original requirement).

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

            text = self.transcriber.transcribe(samples)
            if not text:
                console.print("[dim](heard nothing, back to listening)[/dim]\n")
                continue

            console.print(f"[bold green]you>[/bold green] {text}")
            reply = self.orchestrator.handle(text)
            tag = f"[dim]({self.orchestrator.last_tier.value}: {self.orchestrator.last_model})[/dim]"
            console.print(f"[bold cyan]argus>[/bold cyan] {reply} {tag}\n")

            self._speak_with_barge_in(reply)

    def _speak_with_barge_in(self, text: str) -> None:
        stop_event = threading.Event()

        def _speak_safely():
            try:
                self.speaker.speak(text, stop_event)
            except Exception:
                log.exception("Speech synthesis/playback failed")
                console.print("[red](speech failed -- see log)[/red]")

        speak_thread = threading.Thread(target=_speak_safely)
        speak_thread.start()
        # Best-effort barge-in watcher; if it errors for any reason, speech
        # just plays to completion instead of taking the whole loop down.
        try:
            self._watch_for_barge_in(stop_event, speak_thread)
        except Exception:
            log.exception("Barge-in watcher failed; continuing without it")
        speak_thread.join()

    def _watch_for_barge_in(self, stop_event: threading.Event, speak_thread: threading.Thread) -> None:
        import sounddevice as sd

        # Without this, leftover prediction state from the wake-word model's
        # last real detection can cause an immediate spurious trigger here,
        # stopping playback before anything audible plays.
        self.wake_word.reset()

        chunk = 1280
        with sd.InputStream(samplerate=16000, channels=1, dtype="int16", blocksize=chunk) as stream:
            while speak_thread.is_alive():
                frame, _ = stream.read(chunk)
                if self.wake_word.score_frame(frame.reshape(-1)) > 0.6:
                    console.print("[yellow](barge-in: stopping speech)[/yellow]")
                    stop_event.set()
                    return
