import logging

from rich.console import Console

log = logging.getLogger(__name__)
console = Console()

_YES_WORDS = ("yes", "yeah", "yep", "sure", "go ahead", "do it", "confirm", "please", "okay", "ok")
_NO_WORDS = ("no", "nope", "don't", "do not", "stop", "cancel", "negative", "nah")


def make_voice_confirmer(speaker, transcriber):
    """Confirmations in voice mode were silently falling back to a blocking
    keyboard input() with no spoken prompt -- indistinguishable from a hang
    if a second CONFIRM-tier tool call fired mid-task and you were listening
    for a voice response, not watching the terminal. This speaks the
    question and listens for a spoken yes/no, with typed y/N as the
    fallback if voice confirmation is unclear or fails outright."""

    def confirmer(tool_name: str, tool_input: dict) -> bool:
        from argus.voice.audio_io import record_followup

        console.print(f"\n[confirm] Argus wants to run '{tool_name}' with input: {tool_input}")

        try:
            prompt_text = f"May I {tool_name.replace('_', ' ')}? Say yes or no."
            speaker.speak(prompt_text)
        except Exception:
            log.exception("Failed to speak confirmation prompt")

        try:
            samples = record_followup(6.0)
            if samples is not None:
                heard = transcriber.transcribe(samples).strip()
                if heard:
                    console.print(f"[bold green]you>[/bold green] {heard}")
                    lowered = heard.lower()
                    if any(w in lowered for w in _NO_WORDS):
                        return False
                    if any(w in lowered for w in _YES_WORDS):
                        return True
                    console.print("[dim](didn't catch a clear yes/no)[/dim]")
        except Exception:
            log.exception("Voice confirmation failed")

        console.print("[dim](falling back to typed confirmation)[/dim]")
        answer = console.input("Allow? [y/N] ").strip().lower()
        return answer == "y"

    return confirmer
