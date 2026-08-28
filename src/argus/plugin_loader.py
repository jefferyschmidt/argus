import importlib
import logging
import pkgutil
from pathlib import Path

from argus.tools.base import Tool

log = logging.getLogger(__name__)

PLUGINS_DIR = Path(__file__).resolve().parent / "plugins"


def load_plugin_tools() -> list[Tool]:
    """Scans argus/plugins/*.py for module-level Tool instances -- README
    roadmap item 13 (a clean pattern for adding tools without touching
    core code). A plugin is just a normal Python file defining Tool
    objects exactly the way every built-in tool already does; dropping it
    in argus/plugins/ is the only "registration" step required.

    Isolated failure: a plugin module that fails to import (bad syntax, a
    missing dependency it assumed was installed) is skipped with a logged
    warning, not a crash that takes down every other plugin or the app."""
    tools: list[Tool] = []
    if not PLUGINS_DIR.exists():
        return tools

    for _finder, module_name, _is_pkg in pkgutil.iter_modules([str(PLUGINS_DIR)]):
        if module_name.startswith("_"):
            continue
        try:
            module = importlib.import_module(f"argus.plugins.{module_name}")
        except Exception:
            log.exception("Failed to load plugin '%s' -- skipping", module_name)
            continue
        for value in vars(module).values():
            if isinstance(value, Tool):
                tools.append(value)
                log.info("Loaded plugin tool '%s' from %s", value.name, module_name)
    return tools
