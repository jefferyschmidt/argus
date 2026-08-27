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

    def to_anthropic_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }
