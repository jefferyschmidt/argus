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

import numpy as np
import sounddevice as sd
from rich.console import Console

from argus.config import settings
from argus.persona import CONVERSATION_PROMPT
from argus.tools import build_default_registry
from argus.tools.registry import ToolDenied
from argus.ui import events as ui_events

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


def _make_ui_confirmer():
    """Use the visual console for native-mode confirmations."""
    def confirmer(tool_name: str, tool_input: dict) -> bool:
        from argus.ui import commands as ui_commands

        request_id = ui_commands.request_confirmation(tool_name, tool_input)
        ui_events.publish({
            "type": "confirm_request", "id": request_id,
            "tool_name": tool_name, "tool_input": tool_input,
        })
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
        # Accepts an externally-built registry (ROADMAP.md Phase 1: a
        # single ToolServer shared across every consumer -- chat, this
        # realtime loop, and eventually the proactive engine -- instead of
        # each one silently building its own with no shared task-approval
        # or cost-governor state). Falls back to a fresh one so this class
        # still works standalone, same as before.
        self.tools = tool_registry if tool_registry is not None else build_default_registry()
        self.tools.confirmer = _make_ui_confirmer()
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

    def _session_config(self) -> dict:
        return {
            "type": "realtime",
            "model": settings.openai_realtime_model,
            "instructions": _REALTIME_INSTRUCTIONS,
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
                    if data:
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
                    if transcript:
                        # The audio model may call a tool after this turn;
                        # mark only clear action requests as pre-authorized,
                        # matching the standard orchestrator's behavior.
                        from argus.orchestrator import _should_use_tools
                        with self._speech_lock:
                            self._input_had_transcript = True
                            self._cancel_timer(self._resume_timer)
                            self._resume_timer = None
                        self.tools.reset_task_autonomy(explicitly_requested=_should_use_tools(transcript))
                        if self._audio_is_active():
                            self._clear_pending_audio()
                            if self._response_active:
                                self._send(socket, {"type": "response.cancel"})
                        ui_events.publish({"type": "transcript", "role": "you", "text": transcript})
                        ui_events.publish({"type": "caption", "text": transcript, "role": "you"})
                        self._send(socket, {"type": "response.create"})
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
                    elif self._pending_calls:
                        self._run_pending_tools(socket)
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
