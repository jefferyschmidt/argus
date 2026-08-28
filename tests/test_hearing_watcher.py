import time
from unittest.mock import MagicMock, patch

import numpy as np

from argus.voice.loop import VoiceLoop


def _loop():
    loop = VoiceLoop.__new__(VoiceLoop)  # skip __init__ (loads real models)
    loop.transcriber = MagicMock()
    return loop


def test_hearing_watcher_recovers_after_chunks_out_is_reset():
    """Confirmed live as a real bug, paired with the LocalWakeWordListener
    fix that now clears chunks_out between non-matching utterances: this
    watcher used to assume chunks_out only ever GROWS (len(chunks_out) <=
    last_len was the only skip condition), so a reset back to a shorter
    length left it comparing against a stale high-water mark and it could
    silently stop publishing updates for the new utterance. Uses real
    frame counts of different sizes for the two "utterances" so the test
    can't coincidentally pass via equal lengths, the way real utterances
    essentially never collide on exact sample count either."""
    loop = _loop()
    loop.transcriber.transcribe.side_effect = ["first utterance", "second utterance"]
    published = []

    chunks_out: list = []
    with patch("argus.voice.loop.ui_events.publish", side_effect=lambda e: published.append(e)):
        stop_event = loop._start_hearing_watcher(chunks_out)
        try:
            chunks_out.extend([np.ones(1600, dtype=np.int16)] * 6)  # ~0.6s
            time.sleep(0.9)

            # The listener resetting chunks_out between utterances (the
            # paired fix under test) -- a DIFFERENT frame count than the
            # first utterance's, so a stale high-water-mark bug can't
            # coincidentally look correct here.
            chunks_out.clear()
            chunks_out.extend([np.ones(1600, dtype=np.int16)] * 9)
            time.sleep(0.9)
        finally:
            stop_event.set()
        time.sleep(0.1)

    hearing_texts = [e["text"] for e in published if e.get("type") == "hearing"]
    assert hearing_texts == ["first utterance", "second utterance"]


def test_hearing_watcher_skips_short_bursts_below_the_floor():
    loop = _loop()
    published = []

    chunks_out: list = []
    with patch("argus.voice.loop.ui_events.publish", side_effect=lambda e: published.append(e)):
        stop_event = loop._start_hearing_watcher(chunks_out)
        try:
            chunks_out.append(np.ones(400, dtype=np.int16))  # well under the ~0.5s floor
            time.sleep(0.9)
        finally:
            stop_event.set()
        time.sleep(0.1)

    loop.transcriber.transcribe.assert_not_called()
    assert not [e for e in published if e.get("type") == "hearing"]
