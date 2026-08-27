"""Manual smoke test: real natural-language requests through the actual
orchestrator, auto-approving CONFIRM-tier tools since these two actions
(muting, opening a browser tab) are safe and reversible to test live.
Not part of the pytest suite -- costs real API tokens and has real
side effects on this machine (mutes audio, opens a browser tab)."""

from argus.orchestrator import Orchestrator
from argus.tools import build_default_registry
from argus.tools.registry import ToolRegistry

if __name__ == "__main__":
    registry = ToolRegistry(confirmer=lambda name, inp: True)
    for tool in build_default_registry()._tools.values():
        registry.register(tool)

    orch = Orchestrator(tool_registry=registry)

    print("=== Test 1: mute speakers ===")
    reply = orch.handle("Mute my speakers")
    print("REPLY:", reply)

    print("\n=== Test 2: open browser to YouTube ===")
    reply2 = orch.handle("Open my browser and go to YouTube")
    print("REPLY:", reply2)
