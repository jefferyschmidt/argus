import ollama

from argus.config import settings
from argus.llm.base import CompletionResult, Message, Tier


class OllamaClient:
    """Local CPU-bound model. Good for routing decisions, quick lookups, and
    offline fallback -- not for heavy reasoning on this hardware."""

    def __init__(self, model: str | None = None):
        self.model = model or settings.ollama_local_model
        self._client = ollama.Client(host=settings.ollama_host)

    def complete(self, messages: list[Message], system: str = "") -> CompletionResult:
        chat_messages = []
        if system:
            chat_messages.append({"role": "system", "content": system})
        chat_messages.extend({"role": m.role, "content": m.content} for m in messages)

        response = self._client.chat(model=self.model, messages=chat_messages)
        return CompletionResult(
            text=response["message"]["content"],
            tier=Tier.LOCAL,
            model=self.model,
            input_tokens=response.get("prompt_eval_count", 0),
            output_tokens=response.get("eval_count", 0),
        )

    def is_available(self) -> bool:
        try:
            self._client.list()
            return True
        except Exception:
            return False
