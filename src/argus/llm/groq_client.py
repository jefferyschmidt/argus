from groq import Groq

from argus.config import settings
from argus.llm.base import CompletionResult, Message, Tier


class GroqClient:
    """Hosted, extremely fast inference (Groq's LPU hardware) -- replaces
    Ollama in the low-latency "local" slot (small talk, addressee-gate
    classification) when configured. Ollama's ~15-20s cold-start on this
    CPU-only hardware was the dominant complaint about that path; Groq has
    no cold start and generates at roughly 300-500 tokens/sec.

    Needs internet, unlike genuine local inference -- ModelRouter keeps a
    real OllamaClient around separately (offline_fallback) for the case
    where even this is unreachable, since Groq can't help with a true
    outage the way on-device inference can."""

    def __init__(self, model: str | None = None):
        self.model = model or settings.groq_model
        self._client = Groq(api_key=settings.groq_api_key) if settings.groq_api_key else None

    def complete(self, messages: list[Message], system: str = "") -> CompletionResult:
        chat_messages = []
        if system:
            chat_messages.append({"role": "system", "content": system})
        chat_messages.extend({"role": m.role, "content": m.content} for m in messages)

        response = self._client.chat.completions.create(
            model=self.model,
            messages=chat_messages,
            max_tokens=512,
            # gpt-oss models default to spending most/all of the token
            # budget on hidden reasoning otherwise -- confirmed live,
            # returns empty content at the default effort level. This
            # slot (small talk, addressee classification) doesn't need
            # deep reasoning, just speed and a real answer.
            reasoning_effort="low",
        )
        usage = response.usage
        return CompletionResult(
            text=response.choices[0].message.content or "",
            tier=Tier.LOCAL,
            model=self.model,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
        )

    def is_available(self) -> bool:
        return self._client is not None

    def prewarm(self) -> None:
        pass  # hosted, nothing to warm up -- kept only to match OllamaClient's interface
