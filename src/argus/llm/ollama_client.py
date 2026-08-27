import threading

import ollama

from argus.config import settings
from argus.llm.base import CompletionResult, Message, Tier


class OllamaClient:
    """Local CPU-bound model. Good for routing decisions, quick lookups, and
    offline fallback -- not for heavy reasoning on this hardware.

    On this hardware the ~15-20s latency is almost entirely model-load time
    (cold start), not generation -- a warm model answers in ~1s. keep_alive
    below keeps it resident well past Ollama's 5-minute default, and
    prewarm() fires a throwaway call at startup so the first real message
    of a session doesn't eat the cold-start cost.
    """

    def __init__(self, model: str | None = None):
        self.model = model or settings.ollama_local_model
        self._client = ollama.Client(host=settings.ollama_host)

    def complete(self, messages: list[Message], system: str = "") -> CompletionResult:
        chat_messages = []
        if system:
            chat_messages.append({"role": "system", "content": system})
        chat_messages.extend({"role": m.role, "content": m.content} for m in messages)

        response = self._client.chat(
            model=self.model,
            messages=chat_messages,
            options={"num_predict": 256, "num_ctx": 2048},
            keep_alive="30m",
        )
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

    def prewarm(self) -> None:
        """Fire-and-forget: load the model into memory in the background so
        it's warm by the time the user sends their first message."""
        def _warm():
            try:
                self._client.chat(
                    model=self.model,
                    messages=[{"role": "user", "content": "hi"}],
                    options={"num_predict": 1},
                    keep_alive="30m",
                )
            except Exception:
                pass  # best-effort; is_available()/complete() will surface real errors

        threading.Thread(target=_warm, daemon=True).start()
