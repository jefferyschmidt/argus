"""Manual smoke test: exercises the full tool-use loop against the real
Anthropic API with an auto-approving confirmer, so it runs non-interactively.
Not part of the pytest suite -- costs real API tokens."""

from argus.orchestrator import Orchestrator
from argus.tools import build_default_registry
from argus.tools.registry import ToolRegistry

if __name__ == "__main__":
    registry = ToolRegistry(confirmer=lambda name, inp: True)
    for tool in build_default_registry()._tools.values():
        registry.register(tool)

    orch = Orchestrator(tool_registry=registry)
    reply = orch.handle(
        "Please write a file called hello.txt containing 'hello from argus' "
        "in the workspace, then read it back to confirm, and analyze whether it worked."
    )
    print("REPLY:", reply)
