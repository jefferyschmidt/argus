"""PRD.md §19 unit 40 (Part 1, item 3): the same end-to-end coverage as
tests/test_realtime_e2e.py, replicated against pipeline mode
(voice/loop.py::VoiceLoop) so both modes have equal behavioral coverage
before Part 2's shared-behavior extraction (verified by both suites
staying green across that extraction).

Same fake-the-edges-only discipline as the realtime harness: nothing
about the loop's OWN logic (_process_utterance, _speak_with_barge_in,
_watch_for_barge_in, _barge_in_session, _resume_after_interruption,
_speak_and_open_mic, the confirmer wired via make_voice_confirmer) is
mocked. What's faked is exactly pipeline's edges: STT (a scriptable
FakeTranscriber), TTS (a FakeSpeaker recording what it was asked to say
instead of hitting a real engine), the mic/wake-word audio stream
(FakeInputStream, feeding _watch_for_barge_in's real RMS+VAD-driven
detection loop scripted frames instead of live hardware), and
record_followup (pipeline's real audio-capture call for a follow-up
listen, faked the same way -- return a real orchestrator answer where a
realistic one is needed, None where "nothing heard" is the point).

The loop is built via VoiceLoop.__new__(VoiceLoop), not VoiceLoop() --
already this repo's own established pattern (see
test_barge_in_session.py, test_voice_loop_crash_resilience.py) for
exercising loop methods without VoiceLoop.__init__'s real model
loading (wake-word model, STT, TTS, mic streams).

Where a realtime-specific mechanism has no pipeline analog (there is no
OpenAI response.create/response.cancel protocol, no persisted
conversation-item history for the u24a leak to guard against, and no
per-turn "cancel the in-flight response" concept -- pipeline serializes
everything through one real threading.Lock instead), each test's
docstring says so explicitly and asserts the pipeline-shaped
equivalent instead."""

import queue
import threading
import time

import numpy as np
import pytest

from argus.orchestrator import Orchestrator
from argus.salience.scoring import Candidate, Decision, EscalationStep
from argus.tools.base import PermissionTier, Tool
from argus.tools.registry import ToolRegistry
from argus.ui import commands as ui_commands
from argus.ui import events as ui_events
from argus.voice.confirm import make_voice_confirmer
from argus.voice.loop import VoiceLoop


class FakeInputStream:
    """A scriptable stand-in for sd.InputStream. read() never blocks
    indefinitely -- an empty queue returns a silent frame immediately
    (with a tiny sleep to avoid spinning), matching real hardware
    closely enough for _watch_for_barge_in's own RMS+VAD loop to run
    for real against controlled input."""

    def __init__(self, *args, **kwargs):
        self._frames: "queue.Queue[np.ndarray]" = queue.Queue()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def push_loud(self, n: int = 1, amplitude: int = 20000, size: int = 1280) -> None:
        for _ in range(n):
            self._frames.put(np.full(size, amplitude, dtype=np.int16))

    def push_silence(self, n: int = 1, size: int = 1280) -> None:
        for _ in range(n):
            self._frames.put(np.zeros(size, dtype=np.int16))

    def read(self, frames: int):
        try:
            frame = self._frames.get_nowait()
        except queue.Empty:
            frame = np.zeros(frames, dtype=np.int16)
            time.sleep(0.005)
        return frame.reshape(-1, 1), False


class FakeSpeaker:
    """synthesize()/synthesize_with_visemes() back the normal reply
    path; speak() backs voice/confirm.py's prompt path -- both real
    Speaker implementations expose all three."""

    def __init__(self):
        self.synthesized: list[str] = []
        self.spoken: list[str] = []

    def synthesize_with_visemes(self, text: str):
        return None  # forces the plain-synthesize fallback, like the existing barge-in tests do

    def synthesize(self, text: str):
        self.synthesized.append(text)
        return np.zeros(800, dtype=np.int16), 16000

    def speak(self, text: str) -> None:
        self.spoken.append(text)


class FakeTranscriber:
    def __init__(self, text: str = ""):
        self.text = text

    def transcribe(self, samples) -> str:
        return self.text


class FakeWakeWord:
    def reset(self) -> None:
        pass

    def score_frame(self, frame) -> float:
        return 0.0


class FakeSpeechDetector:
    """`.speaking` is the one thing a test flips to control detection --
    _watch_for_barge_in's hot-mic branch ANDs this with a real RMS
    check against the frame, so a test also has to push a genuinely
    loud frame (see FakeInputStream.push_loud) for both halves of the
    real gate to pass."""

    def __init__(self):
        self.speaking = False

    def reset(self) -> None:
        pass

    def is_speech(self, frame) -> bool:
        return self.speaking


def _fake_play_audio(duration: float = 2.0):
    """A fake argus.voice.audio_io.play_audio: blocks for up to
    `duration`, polling stop_event exactly as the real player polls
    sd.get_stream().active -- so a barge-in detected mid-"playback"
    actually cuts it short, the real mechanism under test."""

    def play(samples, sample_rate, stop_event=None):
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            if stop_event is not None and stop_event.is_set():
                return
            time.sleep(0.02)

    return play


def _wait_until(predicate, timeout=3.0, interval=0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _drain(q: "queue.Queue", timeout: float = 1.0, idle: float = 0.1) -> list[dict]:
    """See test_realtime_e2e.py's identical helper for why this waits
    rather than draining with get_nowait() alone."""
    deadline = time.monotonic() + timeout
    events: list[dict] = []
    while True:
        remaining = (deadline - time.monotonic()) if not events else idle
        if remaining <= 0:
            break
        try:
            events.append(q.get(timeout=remaining))
        except queue.Empty:
            break
    return events


@pytest.fixture
def pipeline_loop(tmp_path, monkeypatch):
    monkeypatch.setattr("argus.config.settings.argus_data_dir", str(tmp_path))
    monkeypatch.setattr("argus.config.settings.spine_db_path", str(tmp_path / "spine.db"))
    monkeypatch.setattr("argus.config.settings.proactive_tick_seconds", 3600.0)
    monkeypatch.setattr("argus.proactive_engine.ProactiveEngine.start", lambda self: None)
    # Default fake for every test -- real sounddevice playback otherwise
    # runs (silently, to whatever device is present) for any test that
    # reaches _speak_with_barge_in without touching barge-in itself.
    # Tests 1/2 override this with their own duration for real barge-in
    # timing; everyone else gets fast, audio-hardware-free completion.
    monkeypatch.setattr("argus.voice.audio_io.play_audio", _fake_play_audio(duration=0.02))

    while ui_commands.get_text_message(timeout=0) is not None:
        pass
    ui_commands.set_voice_confirmation_active(False)

    registry = ToolRegistry()
    calls: list[tuple[str, dict]] = []
    registry.register(Tool(
        name="allow_tool", description="an ALLOW-tier test tool",
        input_schema={"type": "object", "properties": {}}, tier=PermissionTier.ALLOW,
        handler=lambda args: (calls.append(("allow_tool", args)), "allow-tool-result")[1],
    ))
    registry.register(Tool(
        name="confirm_tool", description="a CONFIRM-tier test tool",
        input_schema={"type": "object", "properties": {}}, tier=PermissionTier.CONFIRM,
        handler=lambda args: (calls.append(("confirm_tool", args)), "confirm-tool-result")[1],
    ))

    loop = VoiceLoop.__new__(VoiceLoop)  # bypass __init__ -- no mic/models needed (established pattern)
    loop.orchestrator = Orchestrator(tool_registry=registry)
    loop.calls = calls
    loop.speaker = FakeSpeaker()
    loop.transcriber = FakeTranscriber()
    loop.wake_word = FakeWakeWord()
    loop.speech_detector = FakeSpeechDetector()
    loop._hot_mic_until = 0.0
    loop._interaction_lock = threading.Lock()
    loop._speech_session = None
    loop.orchestrator.tools.confirmer = make_voice_confirmer(loop.speaker, loop.transcriber)

    from argus.proactive_engine import start_proactive_engine
    loop.proactive = start_proactive_engine(loop.orchestrator, loop._speak_and_open_mic, loop._interaction_lock)

    yield loop

    try:
        loop.proactive.spine_engine.stop()
    except Exception:
        pass
    try:
        loop.proactive.escalation_scheduler.stop()
    except Exception:
        pass


# -- 1. Interrupting speech (barge-in) cancels the active response ----------

def test_barge_in_cancels_the_active_response(pipeline_loop, monkeypatch):
    loop = pipeline_loop
    loop._refresh_hot_mic()
    fake_stream = FakeInputStream()
    fake_stream.push_loud(n=5)  # >= _BARGE_IN_HOLD_FRAMES (3)
    monkeypatch.setattr("argus.voice.loop.sd.InputStream", lambda *a, **k: fake_stream)
    monkeypatch.setattr("argus.voice.audio_io.play_audio", _fake_play_audio(duration=2.0))
    loop.speech_detector.speaking = True

    interrupted = loop._speak_with_barge_in("This reply should get cut off partway through.")

    assert interrupted is True


def test_no_barge_in_plays_to_completion(pipeline_loop, monkeypatch):
    """Contrast case, so test 1 is proven discriminating rather than
    always returning True: silence throughout -> plays through fully."""
    loop = pipeline_loop
    loop._refresh_hot_mic()
    fake_stream = FakeInputStream()  # nothing pushed -- FakeInputStream free-runs silence
    monkeypatch.setattr("argus.voice.loop.sd.InputStream", lambda *a, **k: fake_stream)
    monkeypatch.setattr("argus.voice.audio_io.play_audio", _fake_play_audio(duration=0.1))

    interrupted = loop._speak_with_barge_in("A short reply nobody interrupts.")

    assert interrupted is False
    assert loop.speaker.synthesized == ["A short reply nobody interrupts."]


# -- 2. Resuming after a FALSE interruption continues the reply -------------

def test_resume_after_false_barge_in_continues_the_reply(pipeline_loop, monkeypatch):
    """Pipeline's shape here genuinely differs from realtime's: there's
    no server-side response to resume -- a false interruption instead
    triggers ONE short listen (record_followup) to check whether
    anything real was said, and, hearing nothing, fully RE-SYNTHESIZES
    and replays the sentence(s) that got cut off. Faking record_followup
    to return None is exactly what "nothing heard, so resume" means."""
    loop = pipeline_loop
    loop._refresh_hot_mic()

    fake_stream = FakeInputStream()
    fake_stream.push_loud(n=5)  # interrupts sentence 1 partway through its FIRST playback
    monkeypatch.setattr("argus.voice.loop.sd.InputStream", lambda *a, **k: fake_stream)
    monkeypatch.setattr("argus.voice.audio_io.play_audio", _fake_play_audio(duration=1.0))
    monkeypatch.setattr("argus.voice.loop.record_followup", lambda *a, **k: None)  # nothing heard -> false interruption
    loop.speech_detector.speaking = True

    sentences = ["Sentence one.", "Sentence two."]

    def handle_streaming(text, on_sentence):
        for s in sentences:
            on_sentence(s)

    loop.orchestrator.handle_streaming = handle_streaming

    kept_going = loop._process_utterance(text="tell me something")

    assert kept_going is True
    # Confirmed by the captured console output while writing this test:
    # the session watcher (one watcher for the WHOLE reply, started at
    # _barge_in_session's own entry, not per-sentence) detects the
    # pushed loud frames before sentence one's playback even starts --
    # _speak_with_barge_in's own "barged in during the gap before this
    # sentence" short-circuit (see _SpeechSession) then skips synthesis
    # entirely for that first, doomed attempt, exactly to avoid paying
    # for a synthesis call nobody will hear. So there's only ONE
    # synthesize call per sentence in the end, not two -- what actually
    # proves nothing was lost is that BOTH sentences, in order, were
    # eventually synthesized and spoken despite the interruption.
    assert loop.speaker.synthesized == ["Sentence one.", "Sentence two."]


# -- 3. Captions match spoken output, not held/undelivered content ----------

def test_captions_match_only_what_was_actually_delivered(pipeline_loop, monkeypatch):
    """Pipeline's "held" case is the same mechanism realtime's is --
    SalienceDispatcher._deliver()'s interaction_lock.acquire(blocking=
    False) -- just with a real threading.Lock instead of realtime's
    socket-busy adapter. Held content never reaches speak_fn at all, so
    there's nothing pipeline-specific to fake here.

    Stubs SalienceEngine.decide() to a fixed "speak" Decision (matching
    test_proactive_tick_reminders.py's own established convention) so
    delivery success/failure below is driven purely by the real lock,
    not by real scoring math -- confirmed live while writing this test
    that a bare base_urgency=0.9 candidate does NOT reliably reach
    "speak" against real threshold/rhythm-fit math (score dropped to
    0.300 "ambient" on a second submission), the same finding already
    documented for reminder.due candidates elsewhere in this repo."""
    loop = pipeline_loop
    monkeypatch.setattr(loop.proactive.salience_engine, "decide", lambda *a, **k: Decision(action="speak", reason="test"))
    events_q = ui_events.subscribe()
    try:
        candidate = Candidate(observation_id=None, kind="test.caption", subject=None, text="a candidate message", base_urgency=0.9)

        loop._interaction_lock.acquire()  # simulate "Argus mid-conversation"
        try:
            decision = loop.proactive.dispatcher.submit(candidate)
        finally:
            loop._interaction_lock.release()
        assert decision.delivered is False
        held_captions = [e for e in _drain(events_q, timeout=0.3) if e.get("type") == "caption" and "a candidate message" in e.get("text", "")]
        assert held_captions == [], "a held/undelivered candidate must not be captioned"

        decision = loop.proactive.dispatcher.submit(candidate)
        assert decision.delivered is True
        captions = [e for e in _drain(events_q) if e.get("type") == "caption"]
        assert any("a candidate message" in e.get("text", "") for e in captions)
    finally:
        ui_events.unsubscribe(events_q)


# -- 4. Spoken approval of a CONFIRM-tier tool -------------------------------

def test_spoken_yes_approves_a_confirm_tier_tool_and_it_actually_runs(pipeline_loop, monkeypatch):
    """Pipeline has no analog of realtime's u24a leak (a persisted
    conversation.item.create the model keeps obeying) -- there is no
    persisted conversation-item history here at all; the confirmation
    prompt is spoken once via speaker.speak() and never stored anywhere
    the model reads back. The real regression class it guards against
    instead: a spoken "yes" must actually reach and run the real tool
    through the real registry, not just get acknowledged."""
    loop = pipeline_loop
    loop.transcriber.text = "yes"
    monkeypatch.setattr("argus.voice.audio_io.record_followup", lambda *a, **k: np.zeros(100, dtype=np.int16))

    def handle_streaming(text, on_sentence):
        result = loop.orchestrator.tools.execute("confirm_tool", {})
        on_sentence(f"Done -- {result}")

    loop.orchestrator.handle_streaming = handle_streaming

    loop._process_utterance(text="please run the confirm tool")

    assert ("confirm_tool", {}) in loop.calls, f"confirm_tool's real handler never ran; calls={loop.calls}"
    assert any("confirm tool" in p.lower() for p in loop.speaker.spoken), loop.speaker.spoken
    assert any("confirm-tool-result" in s for s in loop.speaker.synthesized), loop.speaker.synthesized


def test_spoken_no_declines_a_confirm_tier_tool(pipeline_loop, monkeypatch):
    """Contrast case, so the approval test above is proven
    discriminating: a clear "no" must not run the tool."""
    loop = pipeline_loop
    loop.transcriber.text = "no"
    monkeypatch.setattr("argus.voice.audio_io.record_followup", lambda *a, **k: np.zeros(100, dtype=np.int16))

    def handle_streaming(text, on_sentence):
        from argus.tools.registry import ToolDenied
        try:
            loop.orchestrator.tools.execute("confirm_tool", {})
            on_sentence("ran it")
        except ToolDenied:
            on_sentence("okay, I won't do that")

    loop.orchestrator.handle_streaming = handle_streaming

    loop._process_utterance(text="please run the confirm tool")

    assert loop.calls == []
    assert any("won't" in s for s in loop.speaker.synthesized), loop.speaker.synthesized


# -- 5. Typed input during a turn is injected as a real user turn -----------

def test_typed_input_is_injected_as_a_real_turn_and_queues_behind_an_active_one(pipeline_loop, monkeypatch):
    """Pipeline's shape genuinely differs from realtime's here too:
    there is no in-flight response to cancel -- everything is
    serialized through one real threading.Lock, so typed input arriving
    mid-turn QUEUES behind the current one instead of interrupting it.
    Both halves are asserted: idle typed input is processed as a normal
    turn, and typed input while the lock is held waits for it, rather
    than either being lost or running concurrently."""
    loop = pipeline_loop
    handled = []

    def handle_streaming(text, on_sentence):
        handled.append(text)
        on_sentence(f"heard: {text}")

    loop.orchestrator.handle_streaming = handle_streaming

    # _external_input_worker's while True: loop has no exit condition at
    # all -- unlike RealtimeVoiceLoop, VoiceLoop carries no self._stop
    # for it. Left running past this test it would keep draining
    # ui_commands' GLOBAL text-message queue for the rest of the suite,
    # racing whatever other test's typed-input message arrives next --
    # confirmed live: without this stop mechanism, a later test in a
    # different file intermittently lost its own message to this test's
    # still-alive worker instead. Wrapping get_text_message to raise
    # once told to stops the thread cleanly once this test is done.
    stop_worker = threading.Event()
    real_get_text_message = ui_commands.get_text_message

    def get_text_message_or_stop(timeout=None):
        if stop_worker.is_set():
            raise SystemExit
        return real_get_text_message(timeout=timeout)

    def _run_worker_until_stopped() -> None:
        try:
            loop._external_input_worker()
        except SystemExit:
            pass  # caught here, not left to propagate to pytest's thread-exception hook

    monkeypatch.setattr("argus.ui.commands.get_text_message", get_text_message_or_stop)
    worker = threading.Thread(target=_run_worker_until_stopped, daemon=True)
    worker.start()
    try:
        # Idle: processed promptly as a real turn.
        ui_commands.submit_text_message("what's on my calendar today")
        assert _wait_until(lambda: "what's on my calendar today" in handled), handled

        # Busy: held until the lock frees up, not dropped, not concurrent.
        loop._interaction_lock.acquire()
        try:
            ui_commands.submit_text_message("and set a reminder too")
            time.sleep(0.3)  # give the worker a moment to dequeue and block on the lock
            assert "and set a reminder too" not in handled, "must not run while the lock is held"
        finally:
            loop._interaction_lock.release()

        assert _wait_until(lambda: "and set a reminder too" in handled), handled
    finally:
        stop_worker.set()
        worker.join(timeout=2.0)
        assert not worker.is_alive(), "leaked _external_input_worker thread would race later tests' typed input"


# -- 6. A tool-call round-trip: the real registry executes it ---------------

def test_tool_call_round_trip_executes_via_the_real_registry(pipeline_loop):
    """Pipeline has no protocol-level "function_call" event the loop
    itself parses -- tool execution happens entirely inside
    Orchestrator.handle_streaming's own model/tool loop (covered
    elsewhere), invisible to VoiceLoop. The loop-level guarantee this
    exercises instead: whatever handle_streaming's real tool execution
    produces reaches _process_utterance's real speak path."""
    loop = pipeline_loop

    def handle_streaming(text, on_sentence):
        result = loop.orchestrator.tools.execute("allow_tool", {"x": 1})
        on_sentence(f"Done -- {result}")

    loop.orchestrator.handle_streaming = handle_streaming

    loop._process_utterance(text="run the allow tool")

    assert ("allow_tool", {"x": 1}) in loop.calls
    assert any("allow-tool-result" in s for s in loop.speaker.synthesized), loop.speaker.synthesized


# -- 7. Proactive announcement delivery, including the escalation follow-up -

def test_proactive_announcement_delivers_via_speak_and_open_mic_and_escalation_tick(pipeline_loop, monkeypatch):
    """PRD §19 u41's own acceptance box, replicated here: must fail
    against the pre-Unit-37 wiring and pass after -- proven the same
    way test_realtime_e2e.py's equivalent test is, by toggling the
    exact collaborator wire unit 37 added.

    A genuine, pre-existing per-mode divergence surfaced while writing
    this test, NOT fixed here (out of scope for Part 1 -- unifying this
    is exactly what Part 2 exists for): EscalationScheduler.process_due()
    calls deliver_fn directly, bypassing SalienceDispatcher._deliver()
    entirely -- so an escalation follow-up is never captioned by
    _deliver() the way a normal dispatcher.submit() delivery is.
    RealtimeVoiceLoop.announce() happens to cover this anyway because it
    unconditionally publishes its own caption regardless of caller;
    VoiceLoop's speak_fn (_speak_and_open_mic -> _speak_with_barge_in)
    does not -- only _speak_unless_thought publishes captions, and
    escalation delivery never goes through it. This test asserts what
    pipeline actually does today (delivers via real synthesis/speech,
    no caption for this specific path) rather than a caption that
    wouldn't reflect real current behavior."""
    loop = pipeline_loop
    engine = loop.proactive
    events_q = ui_events.subscribe()

    def fake_decide(candidate, snapshot, observation=None, now=None):
        return Decision(action="speak", reason="test", escalation=[EscalationStep(after_seconds=1.0, channel="speak")])
    monkeypatch.setattr(engine.salience_engine, "decide", fake_decide)

    candidate = Candidate(observation_id=None, kind="test.escalation", subject=None, text="the original message", base_urgency=0.9)
    now = time.time()

    try:
        engine.dispatcher.escalation = None  # exactly SalienceDispatcher's pre-u37 default
        decision = engine.dispatcher.submit(candidate, now=now)
        assert decision.delivered
        assert engine.escalation_scheduler.pending() == [], "pre-37: nothing should have been scheduled"
        loop.speaker.synthesized.clear()
        engine._tick_escalation(now=now + 5.0)
        assert loop.speaker.synthesized == [], "pre-37: the escalation follow-up must never fire"

        engine.dispatcher.escalation = engine.escalation_scheduler
        decision = engine.dispatcher.submit(candidate, now=now)
        assert decision.delivered
        assert len(engine.escalation_scheduler.pending()) == 1

        loop.speaker.synthesized.clear()
        _drain(events_q)
        engine._tick_escalation(now=now + 5.0)

        assert _wait_until(lambda: "the original message" in loop.speaker.synthesized), loop.speaker.synthesized
        assert engine.escalation_scheduler.pending() == []
        # See the docstring above: escalation delivery bypasses
        # _deliver(), so unlike realtime's announce() there is no
        # caption event for this specific path in pipeline mode today.
        published = _drain(events_q, timeout=0.3)
        assert not any(e.get("type") == "caption" for e in published), (
            "if this starts failing, pipeline now DOES caption escalation delivery -- "
            "update this test's docstring, the divergence it documents is gone"
        )
    finally:
        ui_events.unsubscribe(events_q)


# -- 8. Reminder delivery: spoken, marked notified only after delivery ------

def test_reminder_delivery_speaks_and_marks_notified_only_after_delivery(pipeline_loop, monkeypatch):
    """The other acceptance-critical PRD §19 u41 pair: must fail
    against the pre-Unit-37 wiring (no caller of ReminderStore.list_due()
    existed at all) and pass after."""
    from datetime import datetime

    from argus.memory.reminders import ReminderStore
    from argus.memory.store import get_connection

    loop = pipeline_loop
    engine = loop.proactive
    monkeypatch.setattr(engine.salience_engine, "decide", lambda *a, **k: Decision(action="speak", reason="test"))

    conn = get_connection()
    try:
        store = ReminderStore(conn)
        now = time.time()
        due_iso = datetime.fromtimestamp(now - 60).astimezone().isoformat()
        reminder_id = store.add("call the vet", due_iso)

        due_before = store.list_due(datetime.fromtimestamp(now + 60).astimezone().isoformat())
        assert any(r["id"] == reminder_id for r in due_before), "reminder should still be due/unnotified pre-37"
        assert loop.speaker.synthesized == []

        engine._tick_reminders(now=now)

        assert _wait_until(lambda: any("call the vet" in s for s in loop.speaker.synthesized)), loop.speaker.synthesized

        due_after = store.list_due(datetime.fromtimestamp(now + 60).astimezone().isoformat())
        assert not any(r["id"] == reminder_id for r in due_after), "reminder must be marked notified only after delivery"
    finally:
        conn.close()
