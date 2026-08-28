import threading
import time
from unittest.mock import patch

from argus.ui import commands as ui_commands
from argus.voice.loop import VoiceLoop


def _loop():
    loop = VoiceLoop.__new__(VoiceLoop)  # bypass __init__ -- no mic/models needed
    loop._hot_mic_until = 0.0
    return loop


def test_hot_mic_is_inactive_while_listening_is_paused():
    """Confirmed live as a real gap: the OLD stop-listening mechanism only
    ever affected the hot-mic window -- this covers that it's still
    correctly inactive under the new persistent-pause design too, not
    just the new wait-loop behavior."""
    loop = _loop()
    loop._hot_mic_until = time.monotonic() + 30  # would otherwise be active
    ui_commands.set_listening_paused(True)
    try:
        assert loop._hot_mic_active() is False
    finally:
        ui_commands.set_listening_paused(False)


def test_hot_mic_still_works_normally_when_not_paused():
    loop = _loop()
    loop._hot_mic_until = time.monotonic() + 30
    ui_commands.set_listening_paused(False)

    assert loop._hot_mic_active() is True


def test_wait_while_listening_paused_returns_immediately_when_not_paused():
    loop = _loop()
    ui_commands.set_listening_paused(False)

    started = time.monotonic()
    loop._wait_while_listening_paused()
    elapsed = time.monotonic() - started

    assert elapsed < 0.2


def test_wait_while_listening_paused_blocks_until_unpaused():
    """The actual fix under test: a persistent pause that genuinely holds
    the run loop, not a one-shot flag that only ever touched the hot-mic
    window. Unpauses from another thread partway through, the way the
    console's toggle button really would."""
    loop = _loop()
    ui_commands.set_listening_paused(True)

    def _unpause_shortly():
        time.sleep(0.4)
        ui_commands.set_listening_paused(False)

    with patch("argus.voice.loop.ui_events.publish"):
        threading.Thread(target=_unpause_shortly).start()
        started = time.monotonic()
        loop._wait_while_listening_paused()
        elapsed = time.monotonic() - started

    assert elapsed >= 0.35  # actually waited for the unpause, didn't return early
    assert elapsed < 2.0  # but didn't hang past it either
