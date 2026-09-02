import itertools
import queue
import threading
import time

from argus.config import settings

_listening_paused = threading.Event()
_text_messages: "queue.Queue[str]" = queue.Queue()
_ptt_active = threading.Event()
_quiet_mode = threading.Event()
_proactive_context = threading.Event()
if settings.proactive_context_enabled:
    _proactive_context.set()
_email_watch = threading.Event()
if settings.email_watch_enabled:
    _email_watch.set()


def set_listening_paused(paused: bool) -> None:
    """"Stop listening" from the console -- confirmed live that the old
    one-shot version of this (request_stop_listening/
    consume_stop_listening_request) only ever turned off the hot-mic
    barge-in window specifically, not the mic at all: a live hallucination
    loop (Whisper mis-transcribing near-silence as "thank you" over and
    over, each reply's own echo re-triggering the next) kept right on
    running through the normal wake-word/follow-up loop after the button
    was clicked, because nothing in that loop ever checked it. This is a
    real, persistent PAUSE instead -- the whole run() loop (wake-word wait
    AND the follow-up window) checks it and holds, not just hot-mic --
    same pattern as quiet mode below, not a one-shot flag that gets
    consumed and forgotten."""
    if paused:
        _listening_paused.set()
    else:
        _listening_paused.clear()

    # Confirmed directly requested: an audible cue for listening status
    # "when I'm not staring at the screen." Lazy import -- ui_commands is
    # imported broadly (server.py, orchestrator, chat-only paths that
    # never touch audio at all), and this module pulls in sounddevice.
    try:
        from argus.voice.chime import play_listening_chime, play_stopped_chime
        (play_stopped_chime if paused else play_listening_chime)()
    except Exception:
        pass  # best-effort -- a missing/busy audio device must never break pausing itself


def is_listening_paused() -> bool:
    return _listening_paused.is_set()


def toggle_listening_paused() -> bool:
    """Returns the new state after toggling."""
    new_state = not _listening_paused.is_set()
    set_listening_paused(new_state)
    return new_state


def submit_text_message(text: str) -> None:
    """From the console's text input box -- for when speaking isn't
    practical (noisy room, don't want to talk out loud, etc.)."""
    _text_messages.put(text)


def get_text_message(timeout: float | None = None) -> str | None:
    try:
        return _text_messages.get(timeout=timeout)
    except queue.Empty:
        return None


# Confirmed live as a real bug: typed text sent while a voice confirmation
# (make_voice_confirmer's "May I <tool>? Say yes or no.") was pending got
# silently queued behind the SAME interaction lock the confirmation flow
# was itself blocking on -- only processed as an ordinary new utterance
# once the turn eventually finished some other way. Looked exactly like
# "he's not processing it" because, in the moment, he genuinely wasn't.
# This is a separate channel _external_input_worker routes typed text into
# instead of the normal utterance queue whenever a voice confirmation is
# active, so confirm.py can pick it up directly without touching the lock.
_voice_confirmation_active = threading.Event()
_confirmation_answers: "queue.Queue[str]" = queue.Queue()


def set_voice_confirmation_active(active: bool) -> None:
    if active:
        _voice_confirmation_active.set()
    else:
        _voice_confirmation_active.clear()
        # Drop any stale answer left over from this window so it can't
        # leak into an unrelated later confirmation.
        while not _confirmation_answers.empty():
            try:
                _confirmation_answers.get_nowait()
            except queue.Empty:
                break


def is_voice_confirmation_active() -> bool:
    return _voice_confirmation_active.is_set()


def submit_confirmation_answer(text: str) -> None:
    _confirmation_answers.put(text)


def get_confirmation_answer(timeout: float | None = None) -> str | None:
    try:
        return _confirmation_answers.get(timeout=timeout)
    except queue.Empty:
        return None


def start_push_to_talk() -> None:
    _ptt_active.set()


def stop_push_to_talk() -> None:
    _ptt_active.clear()


def is_push_to_talk_active() -> bool:
    return _ptt_active.is_set()


def set_quiet_mode(enabled: bool) -> None:
    """Text-only mode: Argus keeps listening/responding normally, it just
    never actually speaks the reply out loud -- for when speaking isn't
    practical (late night, a meeting, a quiet room) but you still want the
    full assistant, not just the separate `argus chat` mode."""
    if enabled:
        _quiet_mode.set()
    else:
        _quiet_mode.clear()


def is_quiet_mode() -> bool:
    return _quiet_mode.is_set()


def toggle_quiet_mode() -> bool:
    """Returns the new state after toggling."""
    new_state = not _quiet_mode.is_set()
    set_quiet_mode(new_state)
    return new_state


def set_proactive_context_enabled(enabled: bool) -> None:
    """Global on/off for unprompted check-ins based on what window's
    active (argus/context_awareness.py). Defaults to
    settings.proactive_context_enabled; this is the runtime override."""
    if enabled:
        _proactive_context.set()
    else:
        _proactive_context.clear()


def is_proactive_context_enabled() -> bool:
    return _proactive_context.is_set()


def toggle_proactive_context_enabled() -> bool:
    new_state = not _proactive_context.is_set()
    set_proactive_context_enabled(new_state)
    return new_state


def set_email_watch_enabled(enabled: bool) -> None:
    if enabled:
        _email_watch.set()
    else:
        _email_watch.clear()


def is_email_watch_enabled() -> bool:
    return _email_watch.is_set()


def toggle_email_watch_enabled() -> bool:
    new_state = not _email_watch.is_set()
    set_email_watch_enabled(new_state)
    return new_state


# CONFIRM-tier tool confirmations, resolved through the console UI instead
# of a blocking terminal input() -- confirmed live that dropping to the
# command line for this broke the "everything through the chat/voice
# interface" principle, and typed "yes" there was even being misread as a
# decline (see argus/voice/confirm.py's fix for that separately). A
# monotonic request id guards against a late/stale response (e.g. a
# leftover browser tab) resolving the wrong, newer confirmation.
_confirm_id_counter = itertools.count(1)
_pending_confirm: dict | None = None
_confirm_responses: "queue.Queue[tuple[int, bool]]" = queue.Queue()
_confirm_state_lock = threading.Lock()


def request_confirmation(tool_name: str, tool_input: dict) -> int:
    global _pending_confirm
    request_id = next(_confirm_id_counter)
    with _confirm_state_lock:
        _pending_confirm = {"id": request_id, "tool_name": tool_name, "tool_input": tool_input}
    return request_id


def get_pending_confirmation() -> dict | None:
    with _confirm_state_lock:
        return dict(_pending_confirm) if _pending_confirm else None


def resolve_confirmation(request_id: int, allowed: bool) -> None:
    global _pending_confirm
    with _confirm_state_lock:
        if _pending_confirm and _pending_confirm["id"] == request_id:
            _pending_confirm = None
    _confirm_responses.put((request_id, allowed))


def wait_for_confirmation(request_id: int, timeout: float) -> bool | None:
    """Blocks for up to timeout seconds for a UI response to this specific
    request id, ignoring responses for any other (stale) id. Returns None
    on timeout, and clears the pending-confirmation slot if it's still
    this request sitting there, so a UI card doesn't linger forever for a
    question nobody's going to answer anymore."""
    global _pending_confirm
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            with _confirm_state_lock:
                if _pending_confirm and _pending_confirm["id"] == request_id:
                    _pending_confirm = None
            return None
        try:
            resp_id, allowed = _confirm_responses.get(timeout=remaining)
        except queue.Empty:
            continue
        if resp_id == request_id:
            return allowed


# ---------- active router registry ----------
# The UI server (argus/ui/server.py) runs fully decoupled from any specific
# Orchestrator -- it only ever reads the in-memory event bus, and whichever
# process actually creates an Orchestrator (chat, voice) does so on its own.
# For a server endpoint that needs to make its own LLM call (idle-emote
# generation), it needs SOME live router to call through -- and it must be
# THE SAME router the running conversation uses, not a second one, so spend
# stays on the one shared cost governor/daily cap. Orchestrator.__init__
# registers itself here; the endpoint reads it back, defensively, since a
# bare UI-only preview (no orchestrator running yet) has none.
_active_router = None


def set_active_router(router) -> None:
    global _active_router
    _active_router = router


def get_active_router():
    return _active_router


# Same pattern, for restart.py to flush pending semantic-memory embeds
# before os.execv -- see MemoryManager.flush_pending_embeds.
_active_memory_manager = None


def set_active_memory_manager(memory_manager) -> None:
    global _active_memory_manager
    _active_memory_manager = memory_manager


def get_active_memory_manager():
    return _active_memory_manager


# PRD §15 (Phase H): same pattern again, for GET /api/state and the
# thread-acknowledge/held-dismiss endpoints. The dashboard and the voice
# interface are two projections of ONE world model (§15's governing
# constraint) -- the server must reach the already-constructed
# WorldModel/ThreadStore/SpineStore/RuleInstanceStore living on the
# running ProactiveEngine, never build its own (P4, and P1: uvicorn
# serves on its own thread). ProactiveEngine registers itself here at
# the end of its own __init__, exactly like Orchestrator does for
# set_active_router above; None means no engine is running yet (argus
# chat, or a bare UI preview), which every consumer must handle without
# erroring.
_active_proactive_engine = None


def set_active_proactive_engine(engine) -> None:
    global _active_proactive_engine
    _active_proactive_engine = engine


def get_active_proactive_engine():
    return _active_proactive_engine
