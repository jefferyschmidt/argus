"""PRD.md §19 unit 41: end-to-end voice regression harness, built against
realtime mode (voice/realtime.py) -- the primary mode. This is the layer
unit tests structurally can't be: every test here drives the REAL loop
code (_receive, _run_pending_tools, _ask_voice_confirmation, announce,
submit_text_message, _create_response_or_defer) through a fake
websocket, from an observable INPUT (a socket event, a typed message, a
due reminder) to an observable OUTPUT (an outgoing event actually sent,
a real tool handler actually invoked, a caption actually published, a
reminder actually marked notified) -- never asserting on internal state
alone, and never mocking the loop's own logic.

The only fakes in this file: FakeSocket (a scriptable stand-in for the
real websocket _run_connection opens -- push() feeds an incoming server
event exactly as `for raw in socket` would see it; .sent captures every
outgoing event exactly as the loop's own _send() wrote it) and
sounddevice, which RealtimeVoiceLoop never touches outside
_run_connection/run() -- none of the 8 behaviors below go through
either of those methods, so sounddevice is simply never imported into
the picture, not mocked.

One additional thing is stubbed for test isolation, not faked as part
of the SUT: ProactiveEngine.start() (which spins up every ambient
worker -- sensors, email watcher, routines, research digest, ... -- each
on its own background thread) is a no-op here. None of unit 41's 8
behaviors touch those workers, and leaving them running would make this
suite flaky (real OS/window-focus calls) and slow for no coverage gain.
Everything the 8 behaviors DO touch -- the dispatcher, the escalation
scheduler, the proactive tick's own methods, the tool registry, and
_receive itself -- is still built for real by the real Orchestrator/
ProactiveEngine construction path and driven directly, exactly as
production wires it."""

import json
import queue
import threading
import time

import pytest

from argus.salience.scoring import Candidate, Decision, EscalationStep
from argus.tools.base import PermissionTier, Tool
from argus.tools.registry import ToolRegistry
from argus.ui import commands as ui_commands
from argus.ui import events as ui_events
from argus.voice.realtime import RealtimeVoiceLoop


class FakeSocket:
    """push() feeds one incoming server event; .sent captures every
    outgoing event in order, decoded from JSON. Supports `for raw in
    socket` (what _receive iterates) and .send() (what _send calls)."""

    def __init__(self):
        self._incoming: "queue.Queue[str | None]" = queue.Queue()
        self.sent: list[dict] = []
        self._lock = threading.Lock()

    def send(self, raw: str) -> None:
        with self._lock:
            self.sent.append(json.loads(raw))

    def push(self, event: dict) -> None:
        self._incoming.put(json.dumps(event))

    def stop(self) -> None:
        self._incoming.put(None)

    def __iter__(self):
        return self

    def __next__(self):
        item = self._incoming.get()
        if item is None:
            raise StopIteration
        return item


def _wait_until(predicate, timeout=3.0, interval=0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _sent_types(socket: FakeSocket) -> list[str]:
    return [e.get("type") for e in socket.sent]


def _drain(q: "queue.Queue", timeout: float = 1.0, idle: float = 0.1) -> list[dict]:
    """Collects every event currently pending. A caller typically calls
    this right after a _wait_until() on some OTHER observable (an
    outgoing socket send) -- the corresponding ui_events.publish() a few
    lines later in that same code path, on another thread, can still be
    a beat behind (no ordering guarantee, only near-instant in
    practice), and that straggler isn't necessarily the FIRST item
    already sitting in the queue. Waits up to `timeout` for the first
    event; once at least one has arrived, treats `idle` seconds of
    nothing further as "no more coming.\""""
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
def realtime_loop(tmp_path, monkeypatch):
    monkeypatch.setattr("argus.config.settings.argus_data_dir", str(tmp_path))
    monkeypatch.setattr("argus.config.settings.spine_db_path", str(tmp_path / "spine.db"))
    monkeypatch.setattr("argus.config.settings.openai_api_key", "test-key")
    monkeypatch.setattr("argus.config.settings.proactive_tick_seconds", 3600.0)
    monkeypatch.setattr("argus.config.settings.voice_confirm_speak_timeout_seconds", 2.0)
    monkeypatch.setattr("argus.config.settings.voice_confirm_listen_seconds", 2.0)
    monkeypatch.setattr("argus.proactive_engine.ProactiveEngine.start", lambda self: None)

    # A leftover message/confirmation-answer from a previous test (or a
    # previous failed run) must not leak into this one -- these queues
    # are module-global state in ui/commands.py.
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

    loop = RealtimeVoiceLoop(tool_registry=registry)
    loop.calls = calls  # test-visible record of real tool handler invocations

    yield loop

    loop._stop.set()
    try:
        loop.proactive.spine_engine.stop()
    except Exception:
        pass
    try:
        loop.proactive.escalation_scheduler.stop()
    except Exception:
        pass
    loop._socket = None


def _start_receiving(loop: RealtimeVoiceLoop, socket: FakeSocket) -> None:
    loop._socket = socket
    threading.Thread(target=loop._receive, args=(socket,), daemon=True).start()


# -- 1. Interrupting speech (barge-in) cancels the active response ----------

def test_barge_in_cancels_the_active_response(realtime_loop, monkeypatch):
    monkeypatch.setattr("argus.voice.realtime._BARGE_IN_CONFIRM_SECONDS", 0.05)
    socket = FakeSocket()
    events_q = ui_events.subscribe()
    try:
        _start_receiving(realtime_loop, socket)
        socket.push({"type": "response.created"})  # Argus is mid-speech
        assert _wait_until(lambda: realtime_loop._response_active)

        socket.push({"type": "input_audio_buffer.speech_started"})

        assert _wait_until(lambda: "response.cancel" in _sent_types(socket)), socket.sent
        published = _drain(events_q)
        assert any(e.get("type") == "state" and e.get("value") == "listening" and e.get("mode") == "follow_up" for e in published)
    finally:
        socket.stop()
        ui_events.unsubscribe(events_q)


# -- 2. Resuming after a FALSE interruption restores the response -----------

def test_resume_after_false_barge_in_restores_the_response(realtime_loop, monkeypatch):
    monkeypatch.setattr("argus.voice.realtime._BARGE_IN_CONFIRM_SECONDS", 0.05)
    monkeypatch.setattr("argus.voice.realtime._FALSE_BARGE_IN_RESUME_SECONDS", 0.05)
    socket = FakeSocket()
    try:
        _start_receiving(realtime_loop, socket)
        socket.push({"type": "response.created"})
        assert _wait_until(lambda: realtime_loop._response_active)

        socket.push({"type": "input_audio_buffer.speech_started"})
        assert _wait_until(lambda: "response.cancel" in _sent_types(socket)), "barge-in never confirmed"

        # A FALSE interruption: speech stopped, but no transcript ever
        # arrived (the user made noise, not a follow-up utterance).
        socket.push({"type": "input_audio_buffer.speech_stopped"})

        assert _wait_until(lambda: _sent_types(socket).count("response.create") >= 1), socket.sent
        cancel_idx = _sent_types(socket).index("response.cancel")
        create_idx = _sent_types(socket).index("response.create")
        assert cancel_idx < create_idx, "the response must be cancelled before it's resumed"
    finally:
        socket.stop()


# -- 3. Captions match spoken output, not held/undelivered content ----------

def test_captions_match_only_what_was_actually_delivered(realtime_loop):
    socket = FakeSocket()
    events_q = ui_events.subscribe()
    try:
        _start_receiving(realtime_loop, socket)
        realtime_loop._socket = socket

        # Busy: an announcement attempted now must not be delivered, and
        # must not caption anything -- this is the "not for held content"
        # half of the acceptance box.
        socket.push({"type": "response.created"})
        assert _wait_until(lambda: realtime_loop._response_active)
        delivered = realtime_loop.announce("a held announcement")
        assert delivered is False
        held_captions = [e for e in _drain(events_q) if e.get("type") == "caption" and e.get("text") == "a held announcement"]
        assert held_captions == [], "a held/undelivered announcement must not be captioned"

        # Not busy: the same call now succeeds, and IS captioned with
        # exactly the delivered text.
        socket.push({"type": "response.done"})
        assert _wait_until(lambda: not realtime_loop._response_active)
        delivered = realtime_loop.announce("a delivered announcement")
        assert delivered is True
        captions = [e for e in _drain(events_q) if e.get("type") == "caption"]
        assert any(e.get("text") == "a delivered announcement" for e in captions)
    finally:
        socket.stop()
        ui_events.unsubscribe(events_q)


# -- 4. Spoken approval of a CONFIRM-tier tool (u24a path) -------------------

def test_spoken_yes_approves_a_confirm_tier_tool_and_it_actually_runs(realtime_loop):
    socket = FakeSocket()
    try:
        realtime_loop.tools.reset_task_autonomy(explicitly_requested=False)
        _start_receiving(realtime_loop, socket)
        realtime_loop._socket = socket

        # The model requests the CONFIRM-tier tool, then finishes its turn
        # -- this is what triggers _run_pending_tools on its own thread.
        socket.push({
            "type": "response.output_item.done",
            "item": {"type": "function_call", "name": "confirm_tool", "call_id": "call-confirm-1", "arguments": "{}"},
        })
        socket.push({"type": "response.done"})

        # The confirmer speaks the question as a response-scoped
        # response.create (never a conversation.item.create -- unit 24a).
        def _confirm_prompt_sent():
            return any(
                e.get("type") == "response.create" and "confirm tool" in (e.get("response") or {}).get("instructions", "")
                for e in socket.sent
            )
        assert _wait_until(_confirm_prompt_sent), socket.sent

        # Simulate the question actually being spoken (audio starts, then
        # finishes) -- _ask_voice_confirmation waits for exactly this
        # before it starts listening for an answer.
        socket.push({"type": "response.created"})
        assert _wait_until(lambda: realtime_loop._response_active)
        socket.push({"type": "response.done"})
        assert _wait_until(lambda: not realtime_loop._response_active)

        # The user's spoken "yes" arrives via the exact same event every
        # other turn's transcript uses.
        assert _wait_until(lambda: ui_commands.is_voice_confirmation_active())
        socket.push({
            "type": "conversation.item.input_audio_transcription.completed",
            "transcript": "yes",
        })

        assert _wait_until(lambda: ("confirm_tool", {}) in realtime_loop.calls), (
            f"confirm_tool's real handler never ran; calls={realtime_loop.calls}"
        )
        assert _wait_until(lambda: any(
            e.get("type") == "conversation.item.create" and e.get("item", {}).get("type") == "function_call_output"
            and e.get("item", {}).get("output") == "confirm-tool-result"
            for e in socket.sent
        )), socket.sent

        # u24a regression guard: the confirmation prompt must never have
        # been carried as a conversation.item.create (a persisted item
        # the model would keep obeying on later turns).
        for event in socket.sent:
            if event.get("type") == "conversation.item.create":
                content = event.get("item", {}).get("content", [])
                text = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
                assert "May I confirm tool" not in text, f"confirmation prompt leaked into a conversation item: {event}"
    finally:
        socket.stop()


# -- 5. Typed input during a turn is injected as a real user turn -----------

def test_typed_input_during_a_turn_is_injected_and_cancels_then_resumes(realtime_loop):
    socket = FakeSocket()
    events_q = ui_events.subscribe()
    try:
        _start_receiving(realtime_loop, socket)
        realtime_loop._socket = socket

        # Argus is mid-turn.
        socket.push({"type": "response.created"})
        assert _wait_until(lambda: realtime_loop._response_active)

        # The real entry point the console text box / Telegram bridge
        # use -- not RealtimeVoiceLoop.submit_text_message() called
        # directly, so the loop's own _text_input_worker background
        # thread (started in __init__) is what's actually exercised.
        ui_commands.submit_text_message("what's on my calendar today")

        def _user_turn_injected():
            return any(
                e.get("type") == "conversation.item.create"
                and e.get("item", {}).get("role") == "user"
                and any(c.get("text") == "what's on my calendar today" for c in e.get("item", {}).get("content", []))
                for e in socket.sent
            )
        assert _wait_until(_user_turn_injected), socket.sent
        assert _wait_until(lambda: "response.cancel" in _sent_types(socket)), (
            "typed input mid-turn must cancel the in-flight response, not queue behind it"
        )

        transcripts = [e for e in _drain(events_q) if e.get("type") == "transcript" and e.get("role") == "you"]
        assert any(e.get("text") == "what's on my calendar today" for e in transcripts)

        # The server confirms the cancel actually landed -- the deferred
        # create can now safely fire.
        socket.push({"type": "response.done"})
        assert _wait_until(lambda: _sent_types(socket).count("response.create") >= 1), socket.sent
    finally:
        socket.stop()
        ui_events.unsubscribe(events_q)


# -- 6. A tool-call round-trip: request -> execute -> result -> response ----

def test_tool_call_round_trip_executes_via_the_real_registry(realtime_loop):
    socket = FakeSocket()
    try:
        _start_receiving(realtime_loop, socket)
        realtime_loop._socket = socket

        socket.push({
            "type": "response.output_item.done",
            "item": {"type": "function_call", "name": "allow_tool", "call_id": "call-allow-1", "arguments": '{"x": 1}'},
        })
        socket.push({"type": "response.done"})

        assert _wait_until(lambda: ("allow_tool", {"x": 1}) in realtime_loop.calls), (
            f"allow_tool's real handler never ran; calls={realtime_loop.calls}"
        )

        def _result_sent():
            return any(
                e.get("type") == "conversation.item.create" and e.get("item", {}).get("call_id") == "call-allow-1"
                and e.get("item", {}).get("output") == "allow-tool-result"
                for e in socket.sent
            )
        assert _wait_until(_result_sent), socket.sent
        assert _wait_until(lambda: _sent_types(socket)[-1] == "response.create"), socket.sent
    finally:
        socket.stop()


# -- 7. Proactive announcement delivery, including the escalation follow-up -

def test_proactive_announcement_delivers_via_announce_and_escalation_tick(realtime_loop, monkeypatch):
    """PRD §19 u41 acceptance: this must fail against the pre-Unit-37
    wiring and pass after. Unit 37's own fix here was wiring
    SalienceDispatcher.submit() to actually call
    escalation.schedule() when a delivered "speak" decision carries
    escalation steps -- before that, EscalationScheduler.process_due()
    (already running on its own timer/tick) always found an empty
    table, so an escalation follow-up NEVER actually spoke. Proven
    directly below by toggling the exact collaborator wire unit 37
    added, not by asserting internal state."""
    engine = realtime_loop.proactive
    socket = FakeSocket()
    realtime_loop._socket = socket
    events_q = ui_events.subscribe()

    # A deterministic "speak, with an escalation follow-up" decision --
    # stubbing SalienceEngine.decide()'s OUTPUT (a different, already
    # separately-tested subsystem), not the dispatcher/escalation/
    # announce wiring this test exists to exercise.
    def fake_decide(candidate, snapshot, observation=None, now=None):
        return Decision(action="speak", reason="test", escalation=[EscalationStep(after_seconds=1.0, channel="speak")])
    monkeypatch.setattr(engine.salience_engine, "decide", fake_decide)

    candidate = Candidate(observation_id=None, kind="test.escalation", subject=None, text="the original message", base_urgency=0.9)
    now = time.time()

    try:
        # -- Fails against the pre-Unit-37 wiring --------------------------
        engine.dispatcher.escalation = None  # exactly SalienceDispatcher's pre-u37 default
        decision = engine.dispatcher.submit(candidate, now=now)
        assert decision.delivered  # the original message still goes out immediately
        assert engine.escalation_scheduler.pending() == [], "pre-37: nothing should have been scheduled"
        socket.sent.clear()
        engine._tick_escalation(now=now + 5.0)
        assert socket.sent == [], "pre-37: the escalation follow-up must never fire -- nothing was ever scheduled"

        # -- Passes with the real, current wiring --------------------------
        engine.dispatcher.escalation = engine.escalation_scheduler
        decision = engine.dispatcher.submit(candidate, now=now)
        assert decision.delivered
        pending = engine.escalation_scheduler.pending()
        assert len(pending) == 1 and pending[0].text == "the original message"

        socket.sent.clear()
        _drain(events_q)
        engine._tick_escalation(now=now + 5.0)  # past after_seconds=1.0

        def _escalation_announced():
            return any(
                e.get("type") == "conversation.item.create"
                and "the original message" in json.dumps(e.get("item", {}))
                for e in socket.sent
            ) and "response.create" in _sent_types(socket)
        assert _wait_until(_escalation_announced), socket.sent
        assert engine.escalation_scheduler.pending() == []
        published = _drain(events_q)
        assert any(e.get("type") == "caption" and e.get("text") == "the original message" for e in published)
    finally:
        socket.stop()
        ui_events.unsubscribe(events_q)


# -- 8. Reminder delivery: spoken, marked notified only after delivery ------

def test_reminder_delivery_speaks_and_marks_notified_only_after_delivery(realtime_loop, monkeypatch):
    """PRD §19 u41 acceptance: must fail against the pre-Unit-37 wiring
    and pass after. Before unit 37, NOTHING in either voice loop ever
    called ReminderStore.list_due() -- _tick_reminders (and the whole
    proactive tick it belongs to) did not exist. Proven directly: the
    "before" state is simply not calling it (there was no such call to
    make), and the "after" state is the real production tick step.

    Stubs SalienceEngine.decide() to a fixed "speak" Decision, matching
    test_proactive_tick_reminders.py's own established convention --
    salience's threshold/scoring math is a separate, already-covered
    subsystem; this test is about what happens once a decision comes
    back and reaches announce(), not how that decision is reached."""
    from datetime import datetime

    from argus.memory.reminders import ReminderStore
    from argus.memory.store import get_connection

    engine = realtime_loop.proactive
    monkeypatch.setattr(engine.salience_engine, "decide", lambda *a, **k: Decision(action="speak", reason="test"))
    socket = FakeSocket()
    realtime_loop._socket = socket
    events_q = ui_events.subscribe()

    conn = get_connection()
    store = ReminderStore(conn)
    now = time.time()
    # Same ISO-with-offset shape _tick_reminders itself builds (its own
    # now_iso), since ReminderStore.list_due() compares due_at as a
    # plain string -- a mismatched offset format would silently break
    # the comparison rather than raise.
    due_iso = datetime.fromtimestamp(now - 60).astimezone().isoformat()
    reminder_id = store.add("call the vet", due_iso)

    try:
        # -- Fails against the pre-Unit-37 wiring --------------------------
        # No caller of ReminderStore.list_due() existed at all -- so the
        # reminder simply stays due, unnotified, unspoken.
        due_before = store.list_due(datetime.fromtimestamp(now + 60).astimezone().isoformat())
        assert any(r["id"] == reminder_id for r in due_before), "reminder should still be due/unnotified pre-37"
        assert socket.sent == []

        # -- Passes with the real, current wiring --------------------------
        engine._tick_reminders(now=now)

        def _reminder_spoken():
            return any(
                e.get("type") == "conversation.item.create" and "call the vet" in json.dumps(e.get("item", {}))
                for e in socket.sent
            )
        assert _wait_until(_reminder_spoken), socket.sent
        published = _drain(events_q)
        assert any(e.get("type") == "caption" and "call the vet" in e.get("text", "") for e in published)

        due_after = store.list_due(datetime.fromtimestamp(now + 60).astimezone().isoformat())
        assert not any(r["id"] == reminder_id for r in due_after), "reminder must be marked notified after delivery"
    finally:
        socket.stop()
        ui_events.unsubscribe(events_q)
        conn.close()
