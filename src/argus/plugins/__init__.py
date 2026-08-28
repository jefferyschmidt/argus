"""Drop a .py file in this directory defining one or more module-level
Tool instances (see argus/tools/base.py for the Tool dataclass) and they
get automatically picked up on next startup -- no need to edit
argus/tools/__init__.py or anything else in core code. See
example_dice.py in this directory for a real, working example.

A module here that fails to import (syntax error, missing dependency) is
skipped with a warning rather than breaking every other plugin or the app
as a whole -- see argus/plugin_loader.py."""
