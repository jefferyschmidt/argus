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

# A second, distinct failure mode from the NONE-paraphrase list above: the
# model doesn't say a "nothing" paraphrase, but doesn't generate a genuine
# observation either -- it produces a generic, content-free hedge that
# technically isn't a NONE-paraphrase, so it used to sail straight through
# and get spoken as if it were a real check-in. Observed live, several
# times in a row, with zero connection to whatever actually triggered the
# scan: "worth saying", "something worth saying", "Something genuinely new
# happened.", "Something new." -- exactly the kind of contentless remark
# that makes a proactive check-in feel like a broken script instead of a
# person actually noticing something. "something new" alone is only
# rejected when it's most of the reply (<=6 words) -- as a substring it's
# also legitimate inside real, specific sentences ("I noticed something
# new in your inbox"), which shouldn't get silently dropped.
_CONTENT_FREE_HEDGES = ("worth saying", "genuinely new happened")


def is_none_reply(text: str) -> bool:
    if not text:
        return True
    normalized = text.strip().strip(".!").upper()
    if normalized == "NONE":
        return True
    lowered = text.lower()
    if any(phrase in lowered for phrase in _NONE_PARAPHRASES):
        return True
    if any(phrase in lowered for phrase in _CONTENT_FREE_HEDGES):
        return True
    if "something new" in lowered and len(text.split()) <= 6:
        return True
    return False
