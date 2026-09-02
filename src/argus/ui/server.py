import json
import logging
from dataclasses import asdict
from pathlib import Path

import anyio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from argus.config import settings
from argus.ui import commands as ui_commands
from argus.ui import events as ui_events

log = logging.getLogger(__name__)

app = FastAPI()
STATIC_DIR = Path(__file__).parent / "static"


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/api/config")
def config() -> dict:
    return {
        "piper_voice": settings.piper_voice,
        "cartesia_active": bool(settings.cartesia_api_key),
        "cartesia_voice_id": settings.cartesia_voice_id if settings.cartesia_api_key else None,
        "wake_word_engine": settings.wake_word_engine,
        # Only meaningful for the openwakeword engine -- reporting this
        # unconditionally used to leave the console showing "hey_jarvis_v0.1"
        # even after the local engine (which doesn't use any trained model
        # at all) became the default, actively misleading about what's
        # actually running.
        "wake_word_model": settings.openwakeword_model_name if settings.wake_word_engine == "openwakeword" else None,
        "followup_window_seconds": settings.followup_window_seconds,
        "daily_budget_usd": settings.daily_budget_usd,
        "dashboard_poll_seconds": settings.dashboard_poll_seconds,
    }


@app.get("/api/idle_emote")
def idle_emote() -> dict:
    """Generates one fresh, one-off idle-emote spec (see argus/idle_emote.py)
    for the console's particle face to briefly form while genuinely idle.
    No orchestrator running yet (e.g. a bare UI preview) -- fall back to a
    random built-in spec rather than erroring; the console already treats
    this as best-effort."""
    from argus.idle_emote import generate_idle_emote

    router = ui_commands.get_active_router()
    if router is None:
        import random

        from argus.idle_emote import _FALLBACK_SPECS

        return random.choice(_FALLBACK_SPECS)
    return generate_idle_emote(router)


@app.post("/api/listening/toggle")
def listening_toggle() -> dict:
    new_state = ui_commands.toggle_listening_paused()
    ui_events.publish({"type": "listening_paused", "value": new_state})
    ui_events.publish({
        "type": "toast",
        "text": "Listening paused -- say nothing until you turn it back on." if new_state else "Listening resumed.",
    })
    return {"listening_paused": new_state}


@app.get("/api/listening")
def listening_status() -> dict:
    return {"listening_paused": ui_commands.is_listening_paused()}


@app.post("/api/restart")
def restart() -> dict:
    from argus.restart import request_restart

    ui_events.publish({"type": "toast", "text": "Restarting Argus..."})
    request_restart()
    return {"ok": True}


class TextInput(BaseModel):
    text: str


@app.post("/api/text_input")
def text_input(payload: TextInput) -> dict:
    text = payload.text.strip()
    if not text:
        return {"ok": False, "error": "empty"}
    ui_commands.submit_text_message(text)
    return {"ok": True}


@app.post("/api/ptt_start")
def ptt_start() -> dict:
    ui_commands.start_push_to_talk()
    return {"ok": True}


@app.post("/api/ptt_stop")
def ptt_stop() -> dict:
    ui_commands.stop_push_to_talk()
    return {"ok": True}


@app.post("/api/quiet_mode/toggle")
def quiet_mode_toggle() -> dict:
    new_state = ui_commands.toggle_quiet_mode()
    ui_events.publish({"type": "quiet_mode", "value": new_state})
    return {"quiet_mode": new_state}


@app.get("/api/quiet_mode")
def quiet_mode_status() -> dict:
    return {"quiet_mode": ui_commands.is_quiet_mode()}


@app.post("/api/proactive_context/toggle")
def proactive_context_toggle() -> dict:
    new_state = ui_commands.toggle_proactive_context_enabled()
    ui_events.publish({"type": "proactive_context", "value": new_state})
    return {"proactive_context": new_state}


@app.get("/api/proactive_context")
def proactive_context_status() -> dict:
    return {"proactive_context": ui_commands.is_proactive_context_enabled()}


@app.post("/api/email_watch/toggle")
def email_watch_toggle() -> dict:
    new_state = ui_commands.toggle_email_watch_enabled()
    ui_events.publish({"type": "email_watch", "value": new_state})
    return {"email_watch": new_state}


@app.get("/api/email_watch")
def email_watch_status() -> dict:
    return {"email_watch": ui_commands.is_email_watch_enabled()}


class ConfirmResponse(BaseModel):
    id: int
    allowed: bool


@app.post("/api/confirm")
def confirm(payload: ConfirmResponse) -> dict:
    """Resolves a CONFIRM-tier tool request from the console UI -- the
    fallback path when voice confirmation was unclear (see
    voice/confirm.py). Deliberately not the terminal: confirming a tool
    call should happen through the same interface as everything else."""
    ui_commands.resolve_confirmation(payload.id, payload.allowed)
    return {"ok": True}


@app.get("/api/confirm/pending")
def confirm_pending() -> dict:
    """Lets a freshly-loaded/reconnected console pick up a confirmation
    that was already requested before it connected -- the WS event alone
    would be missed by a page that wasn't open yet."""
    pending = ui_commands.get_pending_confirmation()
    return {"pending": pending}


# PRD §15 (Phase H). Every list in /api/state is capped here regardless
# of how many rows the underlying stores hold -- a dashboard widget needs
# a manageable handful, not everything WorldModel/HeldQueue/RuleStore
# happen to be tracking.
_MAX_STATE_LIST = 50


def _empty_state() -> dict:
    """Same top-level shape as a live response, just empty -- so the
    dashboard's rendering code never has to special-case "no engine yet"
    (argus chat, or a bare UI preview) versus "engine running, nothing
    tracked." Never a 500."""
    return {
        "engine_running": False,
        "now": None,
        "focus": None,
        "open_threads": [],
        "horizon": [],
        "health": [],
        "rhythms": {"baselines": {}},
        "held": [],
        "rules": [],
    }


@app.get("/api/state")
def state() -> dict:
    """PRD §15 unit 28: one projection of the SAME WorldModel/ThreadStore/
    SpineStore the voice loop already runs, reached through
    ui_commands.get_active_proactive_engine() -- the set_active_router
    precedent, applied here. This must never construct a second
    WorldModel/ThreadStore/SpineStore of its own (P4, and P1: uvicorn
    serves this on its own thread)."""
    engine = ui_commands.get_active_proactive_engine()
    if engine is None:
        return _empty_state()

    # WorldModel.snapshot() is already TTL-cached (§4.3,
    # world_snapshot_ttl_seconds) and invalidated early on thread
    # open/close -- calling it plainly here, on every request, is what
    # "honors the existing TTL" means. No second cache belongs here.
    snapshot = engine.world_model.snapshot()
    held = engine.held.pending(limit=_MAX_STATE_LIST)
    rules = engine.orchestrator.rule_store.list_active()[:_MAX_STATE_LIST]
    # PRD §15 unit 30: "active rules (and which are currently firing)" --
    # firing means an active rule_instance is watching something on this
    # rule's behalf right now (Appendix A.3 "Effect reversal"), not
    # merely that the rule is enabled.
    firing_rule_ids = {instance.rule_id for instance in engine.rule_instances.list_active()}

    return {
        "engine_running": True,
        "now": snapshot.now.isoformat(),
        "focus": asdict(snapshot.focus) if snapshot.focus is not None else None,
        "open_threads": [asdict(t) for t in snapshot.open_threads[:_MAX_STATE_LIST]],
        "horizon": [asdict(c) for c in snapshot.horizon[:_MAX_STATE_LIST]],
        "health": [asdict(h) for h in snapshot.health[:_MAX_STATE_LIST]],
        "rhythms": asdict(snapshot.rhythms),
        "held": [asdict(h) for h in held],
        "rules": [
            {
                "id": r.id, "natural_language": r.natural_language, "kind": r.kind,
                "status": r.status, "firing": r.id in firing_rule_ids,
            }
            for r in rules
        ],
    }


@app.post("/api/threads/{thread_id}/acknowledge")
def acknowledge_thread(thread_id: int) -> dict:
    """PRD §15 unit 30 / Appendix A.1: clicking "got it" on a thread
    widget must emit the same thread.acknowledged observation a spoken
    acknowledgment does -- not a parallel path.
    ProactiveEngine.acknowledge_thread() is that one shared mechanism;
    this endpoint just calls it with via="ui"."""
    engine = ui_commands.get_active_proactive_engine()
    if engine is None:
        return {"ok": False, "error": "no engine running"}
    return {"ok": engine.acknowledge_thread(thread_id, via="ui")}


@app.post("/api/held/{item_id}/dismiss")
def dismiss_held_item(item_id: int) -> dict:
    """PRD §15 unit 30: dismissing a held item marks it dismissed and it
    does not reappear -- HeldQueue.dismiss() already implements this;
    this is just the dashboard's door into it."""
    engine = ui_commands.get_active_proactive_engine()
    if engine is None:
        return {"ok": False, "error": "no engine running"}
    return {"ok": engine.held.dismiss(item_id)}


def _resolve_core_memory(memory_id: int, confirmed: bool) -> dict:
    """Direct DB access rather than routing through the live VoiceLoop's
    in-memory objects -- confirm/reject is just a row update, and going
    through the shared SQLite file works whether or not the console
    happens to be attached to the same orchestrator instance."""
    from argus.memory.core import CoreMemoryStore
    from argus.memory.store import get_connection

    conn = get_connection()
    try:
        store = CoreMemoryStore(conn)
        if confirmed:
            store.confirm(memory_id)
        else:
            store.reject(memory_id)
        core_count = len(store.list_confirmed())
    finally:
        conn.close()

    ui_events.publish({"type": "core_memory_resolved", "id": memory_id, "confirmed": confirmed})
    ui_events.publish({"type": "memory", "core": core_count})
    return {"ok": True}


@app.post("/api/core_memory/{memory_id}/confirm")
def confirm_core_memory(memory_id: int) -> dict:
    return _resolve_core_memory(memory_id, confirmed=True)


@app.post("/api/core_memory/{memory_id}/reject")
def reject_core_memory(memory_id: int) -> dict:
    return _resolve_core_memory(memory_id, confirmed=False)


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    q = ui_events.subscribe()
    try:
        while True:
            event = await anyio.to_thread.run_sync(q.get)
            await websocket.send_text(json.dumps(event))
    except WebSocketDisconnect:
        pass
    finally:
        ui_events.unsubscribe(q)


def run(host: str = "127.0.0.1", port: int = 8765) -> None:
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="warning")
