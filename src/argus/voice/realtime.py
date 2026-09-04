"""Optional native speech-to-speech conversation mode via OpenAI Realtime.

This is the low-latency conversation lane: the model hears audio and returns
audio directly, with server-side turn detection and interruption. It also
exposes the same registered Argus tools as the standard voice loop; tool calls
still pass through the shared permission registry.
"""

import base64
import json
import logging
import queue
import threading
import time
from datetime import datetime

import numpy as np
import sounddevice as sd
from rich.console import Console

from argus.config import settings
from argus.persona import CONVERSATION_PROMPT
from argus.tools.registry import ToolDenied
from argus.ui import commands as ui_commands
from argus.ui import events as ui_events
from argus.voice.confirm import _NO_WORDS, _YES_WORDS

log = logging.getLogger(__name__)
console = Console()
_SAMPLE_RATE = 24000
_BLOCK_SIZE = 1200  # 50 ms at the Realtime API's PCM sample rate
_BARGE_IN_CONFIRM_SECONDS = 0.45
_FALSE_BARGE_IN_RESUME_SECONDS = 3.0
_UI_CONFIRM_TIMEOUT_SECONDS = 45.0
_REALTIME_INSTRUCTIONS = CONVERSATION_PROMPT + """

You are in Argus's native conversation mode. Argus normally has desktop,
file, mail, calendar, camera, and web tools, and they are available in this
mode. Use a tool when the user asks for a real action or current information;
otherwise just talk naturally. Never claim Argus lacks those capabilities or
that privacy restrictions prevent them. Tool results are authoritative. Do not
say an action happened until its tool result says it did. Confirmation-required
actions are handled by Argus's normal confirmation system.
"""


def _time_grounding() -> str:
    """PRD §16 unit 34: built exactly as Orchestrator._build_dynamic_system
    does, so both voice modes read identically -- pipeline mode injects
    this fresh every turn; realtime mode never injected it at all,
    leaving the model no basis for "3pm this afternoon" and it
    hallucinated a timezone (observed live: Asia/Kolkata). Callers must
    call this fresh each time (at connect, at reconnect, and on the
    periodic mid-session refresh below) rather than caching the result --
    the whole point is that it's never frozen at import or construction
    time."""
    now = datetime.now().astimezone()
    now_str = now.strftime("%A, %B %d, %Y, %I:%M %p %Z")
    grounding = f"\n\nCurrent date/time: {now_str}"
    if settings.user_location:
        grounding += f"\nUser's location: {settings.user_location}"
    return grounding


class _AnnounceLock:
    """Adapts RealtimeVoiceLoop.announce()'s single non-blocking call into
    the (interaction_lock.acquire/release + speak_fn) shape every proactive
    worker already expects -- see ProactiveEngine. Avoids changing 7
    existing worker classes just to support a second voice loop.

    acquire() only reports whether sending is plausible right now;
    announce() itself re-checks before actually sending, so a worker's
    speak_fn call can still legitimately no-op if state changed in
    between -- the same "best-effort, retried next poll" guarantee the
    real threading.Lock version gives, just without an actual lock to
    hold: OpenAI's server owns turn-taking here, not this process."""

    def __init__(self, loop: "RealtimeVoiceLoop"):
        self._loop = loop

    def acquire(self, blocking: bool = True) -> bool:
        return self._loop._socket is not None and not self._loop._audio_is_active()

    def release(self) -> None:
        pass


def _make_voice_confirmer(loop: "RealtimeVoiceLoop"):
    """Mirrors pipeline mode's make_voice_confirmer (argus/voice/confirm.py):
    speak the question and listen for a spoken yes/no before ever falling
    back to a UI click. Confirmed live as the actual cause of "claimed
    success, still had to click confirm in the UI" -- this confirmer used
    to be UI-click-only with a silent 45s timeout, so a clearly spoken
    "yes" was simply never read at all.

    Realtime mode has no discrete record-then-transcribe step like
    pipeline mode does -- the mic is streamed continuously and transcripts
    arrive asynchronously via the same conversation.item.input_audio_
    transcription.completed event every other turn uses (see _receive).
    This confirmer runs on a background thread (see _run_pending_tools'
    caller in _receive), NOT the receive thread itself -- it has to,
    since it blocks waiting for exactly the event the receive thread is
    the only one reading off the socket. Coordination is via the same
    is_voice_confirmation_active()/submit_confirmation_answer() channel
    pipeline mode's confirm.py already established for racing a typed
    answer against a spoken one -- _receive and submit_text_message both
    check it and divert into that channel instead of starting a normal
    new conversational turn while a confirmation is pending."""

    def confirmer(tool_name: str, tool_input: dict) -> bool:
        request_id = ui_commands.request_confirmation(tool_name, tool_input)
        ui_events.publish({
            "type": "confirm_request", "id": request_id,
            "tool_name": tool_name, "tool_input": tool_input,
        })

        for prompt_text in (
            f"May I {tool_name.replace('_', ' ')}? Say yes or no.",
            "Sorry, didn't catch that -- yes or no?",
        ):
            result = loop._ask_voice_confirmation(prompt_text)
            if result is not None:
                ui_commands.resolve_confirmation(request_id, result)
                ui_events.publish({"type": "confirm_resolved", "id": request_id})
                return result

        allowed = ui_commands.wait_for_confirmation(request_id, _UI_CONFIRM_TIMEOUT_SECONDS)
        ui_events.publish({"type": "confirm_resolved", "id": request_id})
        return bool(allowed)

    return confirmer


class RealtimeVoiceLoop:
    """Continuous, native-audio conversational voice loop.

    OpenAI controls turn detection and barge-in. Audio plays from a small
    queue rather than one TTS call per sentence, eliminating the gaps where
    the normal STT -> text -> TTS chain feels especially mechanical.
    """

    def __init__(self, tool_registry=None):
        if not settings.openai_api_key:
            raise ImportError("OPENAI_API_KEY is required when VOICE_MODE=realtime")
        self._stop = threading.Event()
        self._input: queue.Queue[bytes] = queue.Queue(maxsize=80)
        self._output: queue.Queue[np.ndarray] = queue.Queue(maxsize=160)
        self._playback = np.empty(0, dtype=np.int16)
        self._playback_lock = threading.Lock()
        self._transcript: list[str] = []
        self._output_captioned = False
        self._connection_lost = threading.Event()
        self._connection_error: str | None = None
        self._send_lock = threading.Lock()
        self._pending_calls: list[dict] = []
        self._response_active = False
        self._speech_lock = threading.Lock()
        self._speech_started_at: float | None = None
        self._input_had_transcript = False
        self._barge_in_confirmed = False
        self._barge_in_timer: threading.Timer | None = None
        self._resume_timer: threading.Timer | None = None
        # Set only while a connection is actually open (_run_connection) --
        # lets an external thread (a ROADMAP.md Phase 2 proactive worker)
        # reach this loop's socket via announce() without needing its own
        # reference to whichever connection happens to be live right now.
        self._socket = None
        # Tracks the last expression shown so a repeated generic request
        # ("show me an expression") cycles instead of repeating -- same
        # state orchestrator.py's Orchestrator.last_expression keeps for
        # the pipeline lane.
        self._last_expression: str | None = None
        # Set when a response.cancel was sent and a fresh response.create
        # is wanted right after it actually completes -- see
        # _create_response_or_defer for why "right after" can't mean
        # "immediately."
        self._pending_create_after_cancel = False
        # The response-scoped `instructions` text (if any) that the
        # deferred create above should carry once it actually fires --
        # see _create_response_or_defer and _ask_voice_confirmation (unit
        # 24a). None means an ordinary create with no override.
        self._pending_create_instructions: str | None = None

        # This session's own conversation never goes through Orchestrator
        # (OpenAI's realtime model answers directly) -- but the proactive
        # workers (email watching, reminders, etc.) still need an LLM +
        # memory to decide what's worth mentioning, same as pipeline mode.
        # A dedicated Orchestrator instance here is purely for their use;
        # its cost governor tracks their spend, separate from the realtime
        # session's own OpenAI usage (a different provider Argus doesn't
        # meter).
        #
        # PRD §16 unit 33: construct Orchestrator FIRST, then take its
        # registry -- not the other way around. Orchestrator.__init__
        # only builds the FULL registry (rule_store, spine, task_runner,
        # decision_log, router) when it is given no registry of its own;
        # the previous order here built a bare build_default_registry(...)
        # call with none of those and handed it to Orchestrator, which duly
        # used exactly that bare one instead of building a better one --
        # confirmed live: Argus reported having no rules engine at all in
        # realtime mode, and was silently missing remember_preference,
        # list_rules, revoke_rule, activate_mode/deactivate_mode, standing
        # authorizations, start_task/task_status/cancel_task,
        # query_timeline, compose_document, second_opinion, and
        # scan_document -- every capability that depends on one of those
        # five collaborators. If a tool_registry was passed into this
        # __init__, it's honored here too (the original cf612fd guarantee
        # this must not regress): passed straight through to Orchestrator
        # rather than letting it build a second one, so a caller-shared
        # registry stays shared, not duplicated. Still exactly one
        # registry and one set of MCP bridges either way.
        from argus.orchestrator import Orchestrator
        self.orchestrator = Orchestrator(tool_registry=tool_registry)
        self.tools = self.orchestrator.tools
        self.tools.confirmer = _make_voice_confirmer(self)

        from argus.proactive_engine import start_proactive_engine
        # PRD §19 unit 40 (Part 1): construct-then-start now lives in one
        # shared helper both voice loops and cli.py::chat() call --
        # unchanged behavior here (same construction, same start()).
        self.proactive = start_proactive_engine(self.orchestrator, self.announce, _AnnounceLock(self))

        # Confirmed orphaned before this (ROADMAP.md Phase 2): text input
        # (console box, Telegram) had no consumer at all in realtime mode.
        threading.Thread(target=self._text_input_worker, daemon=True).start()

        # PRD §16 unit 34: 0.0, not time.time() -- the first _session_config()
        # call (at the first connect) must always send fresh grounding
        # regardless of when __init__ ran, so the periodic-refresh check
        # below has nothing to compare against yet.
        self._last_grounding_refresh_ts = 0.0

    def _session_config(self) -> dict:
        # Recomputed on every call, never cached -- this is what makes a
        # reconnect (as well as the first connect) get fresh grounding
        # instead of whatever was true when this loop was constructed.
        self._last_grounding_refresh_ts = time.time()
        return {
            "type": "realtime",
            "model": settings.openai_realtime_model,
            "instructions": _REALTIME_INSTRUCTIONS + _time_grounding(),
            "output_modalities": ["audio"],
            "tools": [
                {
                    "type": "function",
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["input_schema"],
                }
                for tool in self.tools.schemas()
            ],
            "tool_choice": "auto",
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": _SAMPLE_RATE},
                    "noise_reduction": {"type": "near_field"},
                    # Audio is understood natively; this separate
                    # asynchronous transcription is specifically for the
                    # on-screen user transcript and debug record.
                    "transcription": {
                        "model": "gpt-4o-mini-transcribe",
                        **({"language": settings.stt_language} if settings.stt_language else {}),
                    },
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": 0.5,
                        "prefix_padding_ms": 300,
                        "silence_duration_ms": 600,
                        "create_response": False,
                        "interrupt_response": False,
                    },
                },
                "output": {
                    "format": {"type": "audio/pcm", "rate": _SAMPLE_RATE},
                    "voice": settings.openai_realtime_voice,
                },
            },
        }

    def _maybe_refresh_time_grounding(self, socket) -> None:
        """PRD §16 unit 34, long-session staleness: a realtime session can
        run for hours, so the grounding sent at connect drifts. Checked
        on the user's own turns (see _receive's transcript branch) --
        once a refresh is older than settings.realtime_time_refresh_seconds,
        sends a lightweight session.update carrying only regenerated
        instructions, not a full _session_config() resend (which would
        also re-declare tools/audio config nothing here changed). The
        timezone itself -- the actual reported bug -- is already correct
        from the first connect regardless of this; this only keeps clock
        math (e.g. "3pm this afternoon") honest as the session ages."""
        now = time.time()
        if now - self._last_grounding_refresh_ts < settings.realtime_time_refresh_seconds:
            return
        self._send(socket, {
            "type": "session.update",
            "session": {"instructions": _REALTIME_INSTRUCTIONS + _time_grounding()},
        })
        self._last_grounding_refresh_ts = now

    def _should_forward_mic_audio(self) -> bool:
        """False while "listening paused"/mute is on in the console UI --
        checked in the mic-send loop (_run_connection), not here in the
        capture callback, so a captured chunk is always drained from
        self._input either way (nothing to replay once unpaused); this
        only decides whether it actually leaves the process."""
        return not ui_commands.is_listening_paused()

    def _on_input(self, indata, frames, time_info, status) -> None:
        if status:
            log.warning("Realtime microphone status: %s", status)
        try:
            self._input.put_nowait(bytes(indata))
        except queue.Full:
            # Preserve real-time behavior. A stale audio backlog is worse
            # than losing a short frame when the connection hiccups.
            pass

    def _on_output(self, outdata, frames, time_info, status) -> None:
        if status:
            log.warning("Realtime speaker status: %s", status)
        with self._playback_lock:
            while self._playback.size < frames:
                try:
                    self._playback = np.concatenate((self._playback, self._output.get_nowait()))
                except queue.Empty:
                    break
            take = min(frames, self._playback.size)
            outdata.fill(0)
            if take:
                outdata[:take, 0] = self._playback[:take]
                self._playback = self._playback[take:]

    def _clear_pending_audio(self) -> None:
        """Stop locally buffered speech immediately, not just new server audio."""
        with self._playback_lock:
            self._playback = np.empty(0, dtype=np.int16)
            while True:
                try:
                    self._output.get_nowait()
                except queue.Empty:
                    break

    def _audio_is_active(self) -> bool:
        with self._playback_lock:
            return self._response_active or self._playback.size > 0 or not self._output.empty()

    @staticmethod
    def _cancel_timer(timer: threading.Timer | None) -> None:
        if timer is not None:
            timer.cancel()

    def _on_barge_in_started(self, socket) -> None:
        self.tools.reset_task_autonomy()
        with self._speech_lock:
            self._speech_started_at = time.monotonic()
            self._input_had_transcript = False
            self._barge_in_confirmed = False
            self._cancel_timer(self._resume_timer)
            self._resume_timer = None
            self._cancel_timer(self._barge_in_timer)
            self._barge_in_timer = None
            if not self._audio_is_active():
                return
            timer = threading.Timer(_BARGE_IN_CONFIRM_SECONDS, self._confirm_barge_in, args=(socket,))
            timer.daemon = True
            self._barge_in_timer = timer
            timer.start()

    def _confirm_barge_in(self, socket) -> None:
        with self._speech_lock:
            if self._speech_started_at is None or self._input_had_transcript:
                return
            self._barge_in_confirmed = True
        self._clear_pending_audio()
        if self._response_active:
            self._send(socket, {"type": "response.cancel"})
        ui_events.publish({"type": "state", "value": "listening", "mode": "follow_up"})

    def _resume_after_false_barge_in(self, socket) -> None:
        with self._speech_lock:
            if self._speech_started_at is not None or self._input_had_transcript:
                return
            self._barge_in_confirmed = False
        self._send(socket, {"type": "response.create"})

    def _on_barge_in_ended(self, socket) -> None:
        with self._speech_lock:
            self._speech_started_at = None
            self._cancel_timer(self._barge_in_timer)
            self._barge_in_timer = None
            if not self._barge_in_confirmed:
                return
            timer = threading.Timer(_FALSE_BARGE_IN_RESUME_SECONDS, self._resume_after_false_barge_in, args=(socket,))
            timer.daemon = True
            self._resume_timer = timer
            timer.start()

    def _send(self, socket, event: dict) -> None:
        """The mic sender and receiver/tool worker share one WebSocket."""
        with self._send_lock:
            socket.send(json.dumps(event))

    @staticmethod
    def _response_create_payload(instructions: str | None) -> dict:
        payload = {"type": "response.create"}
        if instructions is not None:
            # Response-scoped: applies to exactly this one response and
            # is never added to conversation history, unlike a
            # conversation.item.create (unit 24a) -- see
            # _ask_voice_confirmation for why that distinction matters.
            payload["response"] = {"instructions": instructions}
        return payload

    def _create_response_or_defer(self, socket, instructions: str | None = None) -> None:
        """Confirmed live as a real bug (seen in production, not
        theoretical): sending response.create immediately after
        response.cancel races the server -- OpenAI processes the cancel
        asynchronously, so the create can arrive before the cancellation
        has actually taken effect, and the API rejects it outright:
        "Conversation already has an active response in progress... Wait
        until the response is finished before creating a new one." Every
        call site that wanted to interrupt-then-continue had this same
        race (the transcript-completed handler in _receive, and
        submit_text_message).

        If a response is active, cancel it and defer the create until
        that response's response.done/error confirms the server actually
        considers it finished (see _receive's handling of those events) --
        rather than firing both requests back-to-back and hoping the
        timing works out. `instructions`, if given, is a response-scoped
        override (unit 24a) that must survive into the deferred create
        too, not just the immediate path -- stashed on
        _pending_create_instructions until _receive actually sends it."""
        if self._response_active:
            self._send(socket, {"type": "response.cancel"})
            self._pending_create_after_cancel = True
            self._pending_create_instructions = instructions
        else:
            self._send(socket, self._response_create_payload(instructions))

    def _ask_voice_confirmation(self, prompt_text: str) -> bool | None:
        """Speaks prompt_text and blocks (on whatever thread called this --
        see _run_pending_tools, run off the receive thread specifically so
        this can block) for up to settings.voice_confirm_listen_seconds for
        a matching answer to arrive via the is_voice_confirmation_active()
        channel. Returns True/False on a clear answer, None on silence,
        an unclear answer, or no live connection -- same tri-state contract
        as pipeline mode's _try_voice, so the caller can retry once before
        falling back to the UI.

        Unit 24a: the prompt is carried as a response-scoped `instructions`
        override on the response.create itself, never as a
        conversation.item.create. A conversation item persists for the rest
        of the session -- earlier this was a standing "say this sentence"
        instruction the model kept obeying on every later turn, long after
        the confirmation had resolved. response.instructions applies to
        exactly this one response and is never added to history.

        Unit 24: confirmed live -- the answer window used to open the
        instant the question was SENT, before a word of it had been
        spoken. The realtime sequence is send -> OpenAI generates and
        streams the spoken question (~3-5s) -> the user hears it and
        answers (~1-2s) -> the async transcription arrives (~0.5-1.5s),
        comfortably longer than the old fixed 7s window was already
        running against. Both retry attempts timed out and fell through
        to the console card even when the user did answer. Fix: don't
        start listening until the question has actually finished being
        spoken -- wait for the question's audio to start and then drain
        (poll _audio_is_active()), capped by
        settings.voice_confirm_speak_timeout_seconds so a generation that
        never produces audio can't hang the confirmer or the tool call."""
        socket = self._socket
        if socket is None:
            return None
        ui_events.publish({"type": "state", "value": "listening", "mode": "confirming"})
        ui_commands.set_voice_confirmation_active(True)
        try:
            self._create_response_or_defer(
                socket,
                instructions=f'Say exactly this to the user, word for word, then stop: "{prompt_text}"',
            )
            speak_deadline = time.monotonic() + settings.voice_confirm_speak_timeout_seconds
            while not self._audio_is_active() and time.monotonic() < speak_deadline:
                time.sleep(0.05)
            while self._audio_is_active() and time.monotonic() < speak_deadline:
                time.sleep(0.05)
            heard = ui_commands.get_confirmation_answer(timeout=settings.voice_confirm_listen_seconds)
            if not heard:
                return None
            lowered = heard.strip().lower()
            if any(w in lowered for w in _NO_WORDS):
                return False
            if any(w in lowered for w in _YES_WORDS):
                return True
            return None
        except Exception:
            log.exception("Realtime voice confirmation failed")
            return None
        finally:
            ui_commands.set_voice_confirmation_active(False)
            ui_events.publish({"type": "state", "value": "thinking"})

    def announce(self, text: str) -> bool:
        """ROADMAP.md Phase 2 (ProactiveEngine): the AnnouncementSink this
        loop implements. Realtime mode has no local audio queue to just
        play into like the pipeline VoiceLoop does -- OpenAI's server
        controls turn-taking -- so a proactive announcement has to arrive
        as an injected conversation item instead of a forced audio
        interrupt. Non-blocking, same "try now, caller retries later"
        contract as VoiceLoop's side: returns False immediately (no
        connection, or the user/Argus is actively mid-turn) rather than
        waiting, so a proactive worker's own retry queue handles the rest,
        exactly as it already does for the pipeline loop today."""
        socket = self._socket
        if socket is None or self._audio_is_active():
            return False
        ui_events.publish({"type": "transcript", "role": "argus", "text": text})
        ui_events.publish({"type": "caption", "text": text, "role": "argus"})
        try:
            self._send(socket, {
                "type": "conversation.item.create",
                "item": {
                    "type": "message", "role": "system",
                    "content": [{"type": "input_text", "text": f"(Proactively say this now, in your own words: {text})"}],
                },
            })
            self._send(socket, {"type": "response.create"})
        except Exception:
            log.exception("Realtime proactive announcement failed")
            return False
        return True

    def submit_text_message(self, text: str) -> bool:
        """Injects typed/Telegram text as a real user turn in the live
        conversation. Confirmed orphaned in realtime mode (ROADMAP.md
        Phase 2): both the console's text-input box and the Telegram
        bridge push onto the same queue VoiceLoop._external_input_worker
        drains -- nothing here ever read it, so a message sent while in
        VOICE_MODE=realtime just silently vanished. Unlike announce()
        (best-effort, a proactive worker's own retry queue covers a
        failure), this is text the user actually sent -- see
        _text_input_worker for the retry loop around this."""
        socket = self._socket
        if socket is None:
            return False
        # Races a typed answer against a spoken one during a pending
        # voice confirmation -- same bug class pipeline mode's confirm.py
        # already fixed (a typed "yes" sent mid-confirmation used to just
        # start a whole new conversational turn instead of resolving the
        # pending question).
        if ui_commands.is_voice_confirmation_active():
            ui_commands.submit_confirmation_answer(text)
            ui_events.publish({"type": "transcript", "role": "you", "text": text})
            ui_events.publish({"type": "caption", "text": text, "role": "you"})
            return True
        ui_events.publish({"type": "transcript", "role": "you", "text": text})
        ui_events.publish({"type": "caption", "text": text, "role": "you"})
        try:
            if self._audio_is_active():
                self._clear_pending_audio()
            self._send(socket, {
                "type": "conversation.item.create",
                "item": {
                    "type": "message", "role": "user",
                    "content": [{"type": "input_text", "text": text}],
                },
            })
            self._create_response_or_defer(socket)
        except Exception:
            log.exception("Realtime text-message injection failed")
            return False
        return True

    def _text_input_worker(self) -> None:
        """Drains the same text-message queue VoiceLoop's
        _external_input_worker does (console text box, Telegram bridge --
        see cli.py's unconditional TelegramBridge().start()). A message
        the user actually sent gets a real retry loop, not a silent drop,
        for the ordinary transient case (reconnecting after a dropped
        connection) -- gives up and surfaces a toast only after several
        attempts spread over a few seconds."""
        while not self._stop.is_set():
            text = ui_commands.get_text_message(timeout=0.2)
            if not text:
                continue
            for _ in range(10):
                if self.submit_text_message(text):
                    break
                if self._stop.is_set():
                    return
                time.sleep(0.5)
            else:
                log.warning("Realtime text message could not be delivered: %r", text)
                ui_events.publish({
                    "type": "toast",
                    "text": "Couldn't deliver that message -- the voice connection wasn't ready.",
                })

    def _run_pending_tools(self, socket) -> None:
        """Execute the model's completed function calls, then let it speak
        naturally about the results in a fresh response."""
        calls, self._pending_calls = self._pending_calls, []
        if not calls:
            return
        for call in calls:
            name = call.get("name", "")
            call_id = call.get("call_id", "")
            try:
                arguments = json.loads(call.get("arguments", "{}"))
                if not isinstance(arguments, dict):
                    raise ValueError("tool arguments must be an object")
                tool = self.tools._tools.get(name)
                ui_events.publish({
                    "type": "tool_call", "name": name, "input": arguments,
                    "tier": tool.tier.value if tool else None,
                })
                result = self.tools.execute(name, arguments)
            except ToolDenied as error:
                result = f"error: {error}"
            except Exception as error:
                log.exception("Realtime tool %s failed", name)
                result = f"error: {type(error).__name__}: {error}"

            if isinstance(result, bytes):
                # Image-returning tools publish their visual result through
                # their existing UI hooks; the audio model gets a concise
                # acknowledgement rather than a base64 blob.
                result = f"{name} completed and returned an image."
            self._send(socket, {
                "type": "conversation.item.create",
                "item": {"type": "function_call_output", "call_id": call_id, "output": str(result)},
            })
        self._send(socket, {"type": "response.create"})

    def _receive(self, socket) -> None:
        try:
            for raw in socket:
                if self._stop.is_set():
                    return
                event = json.loads(raw)
                event_type = event.get("type", "")
                if event_type in {"input_audio_buffer.speech_started", "response.created"}:
                    if event_type == "input_audio_buffer.speech_started":
                        self._on_barge_in_started(socket)
                    if event_type == "response.created":
                        self._response_active = True
                        self._output_captioned = False
                    ui_events.publish({"type": "state", "value": "listening" if "speech" in event_type else "thinking"})
                elif event_type == "input_audio_buffer.speech_stopped":
                    self._on_barge_in_ended(socket)
                elif event_type in {"response.output_audio.delta", "response.audio.delta"}:
                    data = event.get("delta", "")
                    # Quiet mode: same contract as the pipeline loop's
                    # _speak_with_barge_in -- the transcript/caption events
                    # below still publish normally, only the actual audio
                    # playback is skipped. OpenAI still generates the audio
                    # server-side either way (output_modalities is fixed at
                    # session-config time); this just never plays it.
                    if data and not ui_commands.is_quiet_mode():
                        samples = np.frombuffer(base64.b64decode(data), dtype=np.int16)
                        try:
                            self._output.put_nowait(samples)
                        except queue.Full:
                            pass
                        ui_events.publish({"type": "state", "value": "speaking"})
                elif event_type in {"response.output_audio_transcript.delta", "response.audio_transcript.delta"}:
                    self._transcript.append(event.get("delta", ""))
                elif event_type in {"response.output_audio_transcript.done", "response.audio_transcript.done"}:
                    transcript = event.get("transcript", "") or "".join(self._transcript)
                    if transcript:
                        ui_events.publish({"type": "caption", "text": transcript, "role": "argus"})
                        self._output_captioned = True
                elif event_type == "conversation.item.input_audio_transcription.completed":
                    transcript = event.get("transcript", "")
                    if transcript and ui_commands.is_voice_confirmation_active():
                        # A confirmer is blocked on a background thread
                        # waiting on exactly this answer (see
                        # _ask_voice_confirmation) -- divert it there
                        # instead of treating it as a new conversational
                        # turn. Must run on this thread since it's the
                        # only one reading events off the socket.
                        ui_commands.submit_confirmation_answer(transcript)
                        ui_events.publish({"type": "transcript", "role": "you", "text": transcript})
                        ui_events.publish({"type": "caption", "text": transcript, "role": "you"})
                    elif transcript:
                        # The audio model may call a tool after this turn;
                        # mark only clear action requests as pre-authorized,
                        # matching the standard orchestrator's behavior.
                        from argus.orchestrator import _detect_requested_expression, _should_use_tools
                        # Confirmed orphaned in realtime mode (ROADMAP.md
                        # Phase 2): a direct request ("show me you're
                        # happy") never triggered the face at all here --
                        # matched deterministically, same as the pipeline
                        # orchestrator, rather than depending on the audio
                        # model to remember an EXPRESSION: marker it was
                        # never even told about (this mode's system prompt
                        # carries no such protocol).
                        requested_expression = _detect_requested_expression(transcript, self._last_expression)
                        if requested_expression:
                            self._last_expression = requested_expression
                            ui_events.publish({"type": "expression", "value": requested_expression})
                        # PRD §15 unit 32: only reachable here, after the
                        # is_voice_confirmation_active() diversion above
                        # has already run and found nothing to divert --
                        # a yes/no answering a permission prompt never
                        # gets here at all. A side effect on an otherwise
                        # normal turn, not a replacement for it: this
                        # never suppresses the rest of this transcript's
                        # handling below, even when it does close a
                        # thread.
                        from argus.voice.acknowledgment import maybe_acknowledge_spoken_thread
                        maybe_acknowledge_spoken_thread(transcript, getattr(self, "proactive", None))
                        self._maybe_refresh_time_grounding(socket)
                        with self._speech_lock:
                            self._input_had_transcript = True
                            self._cancel_timer(self._resume_timer)
                            self._resume_timer = None
                        self.tools.reset_task_autonomy(explicitly_requested=_should_use_tools(transcript))
                        if self._audio_is_active():
                            self._clear_pending_audio()
                        ui_events.publish({"type": "transcript", "role": "you", "text": transcript})
                        ui_events.publish({"type": "caption", "text": transcript, "role": "you"})
                        self._create_response_or_defer(socket)
                elif event_type == "response.output_item.done":
                    item = event.get("item", {})
                    if item.get("type") == "function_call":
                        self._pending_calls.append(item)
                elif event_type in {"response.done", "error"}:
                    self._response_active = False
                    if self._transcript:
                        transcript = "".join(self._transcript)
                        ui_events.publish({"type": "transcript", "role": "argus", "text": transcript})
                        if not self._output_captioned:
                            ui_events.publish({"type": "caption", "text": transcript, "role": "argus"})
                        self._transcript.clear()
                    ui_events.publish({"type": "state", "value": "listening", "mode": "follow_up"})
                    if event_type == "error":
                        log.error("Realtime API error: %s", event.get("error"))
                        # Don't retry a deferred create into an error state
                        # -- that's how a real bug (a request racing an
                        # in-flight cancel) turns into a self-sustaining
                        # error loop instead of just settling.
                        self._pending_create_after_cancel = False
                        self._pending_create_instructions = None
                    elif self._pending_calls:
                        # Run off the receive thread: a CONFIRM-tier tool's
                        # confirmer (_ask_voice_confirmation) blocks
                        # waiting for the user's spoken yes/no, which only
                        # arrives as a transcript event this same receive
                        # loop delivers -- running synchronously here would
                        # deadlock the confirmer against its own answer.
                        threading.Thread(target=self._run_pending_tools, args=(socket,), daemon=True).start()
                        # _run_pending_tools sends its own response.create
                        # once tool results are in -- a deferred one here
                        # would fire a second, redundant response.create.
                        self._pending_create_after_cancel = False
                        self._pending_create_instructions = None
                    elif self._pending_create_after_cancel:
                        # The server has now genuinely confirmed the
                        # previous response is finished (this event is
                        # exactly that confirmation) -- safe to send the
                        # create that was deferred in _create_response_or_defer.
                        # Any response-scoped instructions stashed alongside
                        # the deferral (unit 24a) ride along with this create
                        # too, not just the immediate-send path.
                        self._pending_create_after_cancel = False
                        instructions = self._pending_create_instructions
                        self._pending_create_instructions = None
                        self._send(socket, self._response_create_payload(instructions))
        except Exception:
            if not self._stop.is_set():
                log.exception("Realtime connection stopped")
                self._connection_error = "The voice connection closed unexpectedly."
                self._connection_lost.set()
        else:
            if not self._stop.is_set():
                self._connection_error = "The voice connection closed."
                self._connection_lost.set()

    def _run_connection(self, connect) -> None:
        """Runs one socket connection; the outer loop reconnects if it drops."""
        self._connection_lost.clear()
        self._connection_error = None
        self._clear_pending_audio()
        self._pending_calls.clear()
        url = f"wss://api.openai.com/v1/realtime?model={settings.openai_realtime_model}"
        with connect(
            url,
            additional_headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            open_timeout=15,
        ) as socket, sd.InputStream(
            samplerate=_SAMPLE_RATE, channels=1, dtype="int16", blocksize=_BLOCK_SIZE, callback=self._on_input
        ), sd.OutputStream(
            samplerate=_SAMPLE_RATE, channels=1, dtype="int16", blocksize=_BLOCK_SIZE, callback=self._on_output
        ):
            self._send(socket, {"type": "session.update", "session": self._session_config()})
            self._socket = socket
            receiver = threading.Thread(target=self._receive, args=(socket,), daemon=True)
            receiver.start()
            try:
                while not self._stop.is_set() and not self._connection_lost.is_set():
                    try:
                        chunk = self._input.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    # Confirmed orphaned in realtime mode (ROADMAP.md Phase
                    # 2): "listening paused"/mute never actually muted here
                    # -- the pipeline VoiceLoop's mic loop already checks
                    # this, but this one didn't, so a user who paused
                    # listening in the console UI still had their mic
                    # streamed to OpenAI the whole time. The chunk is
                    # already drained from the queue either way (nothing to
                    # replay once unpaused); this only decides whether it
                    # actually leaves the process.
                    if not self._should_forward_mic_audio():
                        continue
                    self._send(socket, {
                        "type": "input_audio_buffer.append",
                        "audio": base64.b64encode(chunk).decode("ascii"),
                    })
            finally:
                self._socket = None
        if self._connection_lost.is_set() and not self._stop.is_set():
            raise ConnectionError(self._connection_error or "Realtime connection closed")

    def run(self) -> None:
        from websockets.sync.client import connect

        console.print("[bold cyan]Argus[/bold cyan] native voice conversation online. Ctrl+C to quit.\n")
        ui_events.publish({"type": "state", "value": "listening", "mode": "follow_up"})
        try:
            consecutive_failures = 0
            while not self._stop.is_set():
                try:
                    self._run_connection(connect)
                    consecutive_failures = 0
                except Exception as error:
                    consecutive_failures += 1
                    log.warning("Realtime voice connection failed (%s). Reconnecting.", error)
                    console.print("[yellow](voice connection dropped — reconnecting)[/yellow]")
                    ui_events.publish({"type": "state", "value": "listening", "mode": "follow_up"})
                    if consecutive_failures >= 3:
                        raise RuntimeError("Realtime voice could not reconnect after three attempts") from error
                    time.sleep(1.5)
        except KeyboardInterrupt:
            pass
        except Exception:
            log.exception("Realtime voice loop failed")
            console.print("[red](native voice stopped — see data/argus.log for details)[/red]")
        finally:
            self._stop.set()
