"""Manual smoke test: starts the real UI server, connects a WS client, then
runs a live orchestrator turn (real API call) and prints every event
received over the socket. Not part of the pytest suite -- costs real API
tokens and needs the [ui] extra installed."""

import threading
import time

from fastapi.testclient import TestClient

from argus.orchestrator import Orchestrator
from argus.ui.server import app

if __name__ == "__main__":
    client = TestClient(app)
    events = []

    def listen():
        with client.websocket_connect("/ws") as ws:
            while True:
                events.append(ws.receive_json())

    t = threading.Thread(target=listen, daemon=True)
    t.start()
    time.sleep(0.3)  # let the subscriber register before publishing starts

    orch = Orchestrator()
    orch.handle("What's a good name for a watchful AI, one word?")

    time.sleep(0.5)
    print(f"Received {len(events)} events:")
    for e in events:
        print(" -", {k: v for k, v in e.items() if k != "image"})
