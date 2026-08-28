import logging

from rich.console import Console

log = logging.getLogger(__name__)
console = Console()

_YES_WORDS = ("yes", "yeah", "yep", "sure", "go ahead", "do it", "confirm", "please", "okay", "ok")
_NO_WORDS = ("no", "nope", "don't", "do not", "stop", "cancel", "negative", "nah")
_UI_FALLBACK_TIMEOUT_SECONDS = 45.0


def make_voice_confirmer(speaker, transcriber):
    """Confirmations in voice mode were originally falling back to a
    blocking keyboard input() with no spoken prompt -- indistinguishable
    from a hang if a second CONFIRM-tier tool call fired mid-task and you
    were listening for a voice response, not watching the terminal. This
    speaks the question and listens for a spoken yes/no first.

    If that's unclear, the fallback is the console UI (a card with
    Allow/Deny buttons), NOT the terminal -- confirmed live that dropping
    to the command line there broke "everything through the chat/voice
    interface," and separately that its old y/N prompt was silently
    misreading a typed "yes" as a decline. If the UI never responds either
    (nobody's looking at it, or the console server isn't running) it times
    out to a safe default of declining, with a spoken explanation, rather
    than hanging forever."""

    def confirmer(tool_name: str, tool_input: dict) -> bool:
        from argus.ui import commands as ui_commands
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

        from argus.ui import events as ui_events

        console.print("[dim](waiting for a response in the console...)[/dim]")
        request_id = ui_commands.request_confirmation(tool_name, tool_input)
        ui_events.publish({
            "type": "confirm_request", "id": request_id,
            "tool_name": tool_name, "tool_input": tool_input,
        })
        allowed = ui_commands.wait_for_confirmation(request_id, _UI_FALLBACK_TIMEOUT_SECONDS)
        ui_events.publish({"type": "confirm_resolved", "id": request_id})
        if allowed is None:
            console.print("[dim](no response in the console -- declining)[/dim]")
            try:
                speaker.speak("I didn't hear back, so I'll hold off on that for now.")
            except Exception:
                log.exception("Failed to speak confirmation timeout notice")
            return False
        return allowed

    return confirmer
