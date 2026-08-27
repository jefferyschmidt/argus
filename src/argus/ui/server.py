import json
import logging
from pathlib import Path

import anyio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from argus.config import settings
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
