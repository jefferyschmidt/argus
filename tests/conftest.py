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


@pytest.fixture(autouse=True)
def _isolated_event_log(tmp_path):
    """ui_events.publish() -- the low-level choke point almost everything
    goes through (orchestrator, voice loop, every proactive worker) --
    unconditionally appends to the real data/events/events-YYYY-MM-DD.jsonl
    file, with no test-mode override. Confirmed live as a real bug: running
    this suite polluted the actual production event log with fabricated
    test transcripts (test fixture placeholder text, indistinguishable
    from genuine conversation) that corrupted a live debugging session
    reading that exact file for real diagnosis. Autoused so no individual
    test has to remember to isolate it; test_events_log.py's own
    `with patch("argus.ui.events._event_log_path", ...)` blocks still work
    layered under this -- an inner patch on the same target simply shadows
    this one for its own scope."""
    with patch("argus.ui.events._event_log_path", return_value=tmp_path / "events-test.jsonl"):
        yield


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    """Same rationale as _isolated_event_log above, for the same reason:
    since U-C4, constructing a ProactiveEngine (which VoiceLoop/
    RealtimeVoiceLoop both do in __init__) also constructs SpineStore/
    ThreadStore/RuleStore/InterruptionBudget/HeldQueue/RhythmStore, all of
    which default to settings.data_dir -- the real production data/
    directory -- when no explicit path is given. Without this, any test
    that constructs one of those for real (not fully mocked) would create
    tables and rows in the actual argus.db/spine.db. Tests that pass an
    explicit tmp_path-based path to a store already bypass settings.data_dir
    entirely, so this doesn't interfere with them."""
    monkeypatch.setattr("argus.config.settings.argus_data_dir", str(tmp_path))
