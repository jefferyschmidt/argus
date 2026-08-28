import json
import logging
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
        "wake_word_model": settings.wake_word_model,
        "followup_window_seconds": settings.followup_window_seconds,
        "daily_budget_usd": settings.daily_budget_usd,
    }


@app.post("/api/stop_listening")
def stop_listening() -> dict:
    ui_commands.request_stop_listening()
    ui_events.publish({"type": "toast", "text": "Hot mic turned off from the console."})
    return {"ok": True}


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
