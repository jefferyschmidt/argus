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
    # CONFIRM-tier tools that are cheap, reversible, and typically called
    # many times back-to-back within one task (click, type_text, press_key)
    # -- confirmed live that asking every single time made multi-step
    # desktop automation unusable ("asking for confirmation for every
    # click"). Approving one call of a repeatable tool auto-approves the
    # rest of that same tool for the rest of the current task; the slate
    # is wiped at the start of each new user-initiated turn.
    repeatable: bool = False

    def to_anthropic_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }
