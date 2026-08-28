import re

# Two fixed-width lookbehind branches: the plain "ends in .!?" case,
# and the same followed by one closing bracket or quote. Without the
# second, a sentence closed inside a bracket or quote -- `(Checking the
# calendar first.) Here's what I found.` or `He said "hello." Then left.`
# -- never split at that boundary, because the character immediately
# before the space is `)` or `"`, not `.`. That matters beyond tidiness:
# a parenthesized internal thought has to arrive as its OWN sentence for
# the voice loop to recognize and skip speaking it (see _is_thought).
_SENTENCE_END = re.compile(r"""(?:(?<=[.!?])|(?<=[.!?][)\]"']))\s+""")


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
