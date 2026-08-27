import queue
import threading

_stop_listening_requested = threading.Event()
_text_messages: "queue.Queue[str]" = queue.Queue()
_ptt_active = threading.Event()


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
