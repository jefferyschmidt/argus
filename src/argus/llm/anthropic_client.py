import anthropic

from argus.config import settings
from argus.llm.base import CompletionResult, Message, Tier

_TIER_MODEL = {
    Tier.FAST: lambda: settings.anthropic_model,
    Tier.ADVANCED: lambda: settings.anthropic_advanced_model,
}


class AnthropicClient:
    """Frontier tier. Does the real reasoning since local generation is
    CPU-bound on this machine."""

    def __init__(self):
        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    def complete(
        self, messages: list[Message], system: str = "", tier: Tier = Tier.FAST
    ) -> CompletionResult:
        model = _TIER_MODEL[tier]()
        response = self._client.messages.create(
            model=model,
            max_tokens=4096,
            system=system or anthropic.NOT_GIVEN,
            messages=[{"role": m.role, "content": m.content} for m in messages],
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        return CompletionResult(
            text=text,
            tier=tier,
            model=model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
