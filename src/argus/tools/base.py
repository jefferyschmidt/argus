from dataclasses import dataclass
from enum import Enum
from typing import Callable


class PermissionTier(str, Enum):
    ALLOW = "allow"      # runs unsupervised
    CONFIRM = "confirm"  # requires an explicit yes before running
    DENY = "deny"        # registered but never executable (documents intent / future work)


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    tier: PermissionTier
    handler: Callable[[dict], str | bytes]  # bytes handler output is treated as a PNG image
    # Extra confirmation gate for CONFIRM-tier tools whose consequence is
    # irreversible or visible to someone else (send_email, restart_argus,
    # commit_own_changes, write_own_source) -- a single misheard "yes" is a
    # real, documented risk in this codebase (STT mishearing is called out
    # repeatedly elsewhere), and these are exactly the actions where that's
    # most costly. ToolRegistry.execute() asks twice for these, not once.
    high_risk: bool = False

    def to_anthropic_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }
