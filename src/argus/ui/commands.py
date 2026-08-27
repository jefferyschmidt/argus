import threading

_stop_listening_requested = threading.Event()


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
