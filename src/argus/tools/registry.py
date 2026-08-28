import logging
from typing import Callable

from argus.tools.base import PermissionTier, Tool

log = logging.getLogger(__name__)

# Confirmer takes (tool_name, tool_input) and returns True/False. Swappable so
# voice mode can confirm by speech instead of console input later.
Confirmer = Callable[[str, dict], bool]


def console_confirmer(tool_name: str, tool_input: dict) -> bool:
    print(f"\n[confirm] Argus wants to run '{tool_name}' with input: {tool_input}")
    answer = input("Allow? [y/N] ").strip().lower()
    # A strict answer == "y" silently treated "yes" -- a completely
    # natural thing to type -- as a decline, with no error or feedback.
    return answer in ("y", "yes", "yeah", "yep", "sure", "ok", "okay")


class ToolDenied(Exception):
    pass


class ToolRegistry:
    def __init__(self, confirmer: Confirmer = console_confirmer):
        self._tools: dict[str, Tool] = {}
        self.confirmer = confirmer

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def schemas(self) -> list[dict]:
        return [t.to_anthropic_schema() for t in self._tools.values() if t.tier != PermissionTier.DENY]

    def execute(self, name: str, tool_input: dict) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return f"error: unknown tool '{name}'"

        if tool.tier is PermissionTier.DENY:
            return f"error: tool '{name}' is registered but disabled (deny tier)"

        if tool.tier is PermissionTier.CONFIRM:
            if not self.confirmer(name, tool_input):
                log.info("Tool call denied by user: %s(%s)", name, tool_input)
                raise ToolDenied(f"user declined to run '{name}'")

        log.info("Executing tool: %s(%s) [tier=%s]", name, tool_input, tool.tier.value)
        return tool.handler(tool_input)
