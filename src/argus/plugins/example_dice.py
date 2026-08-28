"""Example plugin (README roadmap item 13) -- proves the plugin system
works end to end with a real, harmless capability. Delete this file (or
move it out of argus/plugins/) if you don't want the dice-rolling tool;
it's here purely to demonstrate the pattern, nothing here is load-bearing
for the rest of Argus."""

import random

from argus.tools.base import PermissionTier, Tool


def _roll_dice(args: dict) -> str:
    sides = args.get("sides") or 6
    count = args.get("count") or 1
    if not isinstance(count, int) or not (1 <= count <= 20):
        return "error: count must be a whole number between 1 and 20"
    if not isinstance(sides, int) or not (2 <= sides <= 1000):
        return "error: sides must be a whole number between 2 and 1000"
    rolls = [random.randint(1, sides) for _ in range(count)]
    return f"Rolled {count}d{sides}: {rolls} (total: {sum(rolls)})"


roll_dice_tool = Tool(
    name="roll_dice",
    description="Rolls dice, e.g. 2d6 or 1d20. Just for fun -- no side effects.",
    input_schema={
        "type": "object",
        "properties": {
            "sides": {"type": "integer", "description": "Sides per die (default 6)."},
            "count": {"type": "integer", "description": "Number of dice to roll (default 1)."},
        },
    },
    tier=PermissionTier.ALLOW,
    handler=_roll_dice,
)
