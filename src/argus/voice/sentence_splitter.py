import re

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


class SentenceBuffer:
    """Accumulates streamed text deltas and yields complete sentences as
    soon as they're available, so TTS can start on sentence 1 while later
    sentences are still being generated -- this is what actually closes
    most of the perceived latency gap vs. waiting for the full reply."""

    def __init__(self):
        self._buf = ""

    def add(self, delta: str) -> list[str]:
        self._buf += delta
        parts = _SENTENCE_END.split(self._buf)
        if len(parts) <= 1:
            return []
        complete, self._buf = parts[:-1], parts[-1]
        return [p.strip() for p in complete if p.strip()]

    def flush(self) -> str | None:
        text = self._buf.strip()
        self._buf = ""
        return text or None
