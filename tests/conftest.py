from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _no_real_listening_chimes():
    """set_listening_paused fires a background audio chime on every call
    (argus/voice/chime.py) -- auto-patched out for the whole suite so
    tests never attempt real audio device access or leave stray
    background threads behind. Tests that specifically want to assert
    chime behavior (test_chime.py, test_listening_paused_chime.py) patch
    sd.play/the chime functions themselves, which still works layered
    under this."""
    with patch("argus.voice.chime.play_listening_chime"), patch("argus.voice.chime.play_stopped_chime"):
        yield
