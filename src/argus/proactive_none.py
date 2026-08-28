"""Shared "is this proactive worker's reply actually silence" check for
context_awareness.py, stuck_detection.py, and research_digest.py -- all
three ask a cheap/local model "is this worth interrupting for?" with an
explicit "reply with exactly: NONE" escape hatch, so most scans produce
nothing.

Confirmed live as a real, recurring bug: an exact `text.upper() == "NONE"`
match is brittle against a cheap model that doesn't follow the literal-
token instruction precisely -- a trailing period, or paraphrasing
("there's nothing worth saying right now") instead of the literal word --
and the near-miss got spoken out loud verbatim instead of being treated
as silence, observed live as Argus randomly saying "worth saying" or
"something worth saying" with no context, repeatedly."""

_NONE_PARAPHRASES = ("nothing worth", "nothing genuinely", "nothing new", "nothing noteworthy")


def is_none_reply(text: str) -> bool:
    if not text:
        return True
    normalized = text.strip().strip(".!").upper()
    if normalized == "NONE":
        return True
    lowered = text.lower()
    return any(phrase in lowered for phrase in _NONE_PARAPHRASES)
