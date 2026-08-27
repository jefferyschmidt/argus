"""Manual smoke test: runs a real autonomous agent goal against the live
API with an auto-approving confirmer, so it runs non-interactively. Not
part of the pytest suite -- costs real API tokens."""

from argus.agent.runner import AgentRunner
from argus.tools import build_default_registry
from argus.tools.registry import ToolRegistry

if __name__ == "__main__":
    registry = ToolRegistry(confirmer=lambda name, inp: True)
    for tool in build_default_registry()._tools.values():
        registry.register(tool)

    runner = AgentRunner(tool_registry=registry, max_iterations=10, max_wall_seconds=120)
    result = runner.run(
        "Write a short haiku about a watchful AI assistant to a file called "
        "haiku.txt in the workspace, then read it back to confirm it saved correctly."
    )
    print("RESULT:", result)
    print("AUDIT LOG:", runner.audit.path)
