from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class Tier(str, Enum):
    LOCAL = "local"
    FAST = "fast"
    ADVANCED = "advanced"


@dataclass
class Message:
    role: str
    content: str


@dataclass
class CompletionResult:
    text: str
    tier: Tier
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


class LLMClient(Protocol):
    def complete(self, messages: list[Message], system: str = "") -> CompletionResult: ...
