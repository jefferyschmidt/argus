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


def _split_respecting_parens(text: str) -> list[str]:
    """Same job as _SENTENCE_END.split(), except a candidate boundary
    INSIDE an open "(" is skipped.

    Confirmed live as a real bug: a model reply with two consecutive
    thoughts and no space between them -- "(I'll search for an image.)(The
    results don't have one.)" -- split at the sentence-ending "." inside
    the FIRST thought too (candidates only ever looked at the punctuation
    immediately before the whitespace, never whether it sat inside an open
    paren), so each thought arrived as an unbalanced fragment.
    _is_thought's strict "the opening paren must close on the very last
    character" check then correctly rejected both fragments as not a
    valid thought -- and they got spoken aloud in full, "()" and all.

    Depth is a single running count across the whole text -- a boundary is
    only accepted once every "(" seen so far has been closed, not just the
    ones since the last accepted boundary, so this only ever behaves
    differently from a plain regex split when parens are actually
    unbalanced at that point."""
    depth = 0
    scanned_to = 0
    splits = []
    for m in _SENTENCE_END.finditer(text):
        segment = text[scanned_to:m.start() + 1]
        depth = max(0, depth + segment.count("(") - segment.count(")"))
        scanned_to = m.start() + 1
        if depth == 0:
            splits.append(m.end())
    if not splits:
        return [text]
    parts, prev = [], 0
    for end in splits:
        parts.append(text[prev:end])
        prev = end
    parts.append(text[prev:])
    return parts


class SentenceBuffer:
    """Accumulates streamed text deltas and yields complete sentences as
    soon as they're available, so TTS can start on sentence 1 while later
    sentences are still being generated -- this is what actually closes
    most of the perceived latency gap vs. waiting for the full reply."""

    def __init__(self):
        self._buf = ""

    def add(self, delta: str) -> list[str]:
        self._buf += delta
        parts = _split_respecting_parens(self._buf)
        if len(parts) <= 1:
            return []
        complete, self._buf = parts[:-1], parts[-1]
        return [p.strip() for p in complete if p.strip()]

    def flush(self) -> str | None:
        text = self._buf.strip()
        self._buf = ""
        return text or None
