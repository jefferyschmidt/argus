import queue
import threading

from argus.config import settings

_stop_listening_requested = threading.Event()
_text_messages: "queue.Queue[str]" = queue.Queue()
_ptt_active = threading.Event()
_quiet_mode = threading.Event()
_proactive_context = threading.Event()
if settings.proactive_context_enabled:
    _proactive_context.set()


def request_stop_listening() -> None:
    _stop_listening_requested.set()


def consume_stop_listening_request() -> bool:
    """Returns True (and clears the flag) if a stop-listening request has
    come in since the last check. Polled from the voice loop rather than
    pushed, since it only needs to be checked at natural pause points."""
    if _stop_listening_requested.is_set():
        _stop_listening_requested.clear()
        return True
    return False


def submit_text_message(text: str) -> None:
    """From the console's text input box -- for when speaking isn't
    practical (noisy room, don't want to talk out loud, etc.)."""
    _text_messages.put(text)


def get_text_message(timeout: float | None = None) -> str | None:
    try:
        return _text_messages.get(timeout=timeout)
    except queue.Empty:
        return None


def start_push_to_talk() -> None:
    _ptt_active.set()


def stop_push_to_talk() -> None:
    _ptt_active.clear()


def is_push_to_talk_active() -> bool:
    return _ptt_active.is_set()


def set_quiet_mode(enabled: bool) -> None:
    """Text-only mode: Argus keeps listening/responding normally, it just
    never actually speaks the reply out loud -- for when speaking isn't
    practical (late night, a meeting, a quiet room) but you still want the
    full assistant, not just the separate `argus chat` mode."""
    if enabled:
        _quiet_mode.set()
    else:
        _quiet_mode.clear()


def is_quiet_mode() -> bool:
    return _quiet_mode.is_set()


def toggle_quiet_mode() -> bool:
    """Returns the new state after toggling."""
    new_state = not _quiet_mode.is_set()
    set_quiet_mode(new_state)
    return new_state


def set_proactive_context_enabled(enabled: bool) -> None:
    """Global on/off for unprompted check-ins based on what window's
    active (argus/context_awareness.py). Defaults to
    settings.proactive_context_enabled; this is the runtime override."""
    if enabled:
        _proactive_context.set()
    else:
        _proactive_context.clear()


def is_proactive_context_enabled() -> bool:
    return _proactive_context.is_set()


def toggle_proactive_context_enabled() -> bool:
    new_state = not _proactive_context.is_set()
    set_proactive_context_enabled(new_state)
    return new_state
