from argus.tools.base import PermissionTier, Tool
from argus.tools.filesystem import list_dir_tool, read_file_tool, run_shell_tool, write_file_tool
from argus.tools.registry import ToolRegistry, console_confirmer


def build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for tool in (read_file_tool, list_dir_tool, write_file_tool, run_shell_tool):
        registry.register(tool)
    return registry


__all__ = [
    "PermissionTier",
    "Tool",
    "ToolRegistry",
    "console_confirmer",
    "build_default_registry",
]
