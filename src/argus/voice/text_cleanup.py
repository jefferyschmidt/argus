import re

_CODE_FENCE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`]*)`")
_BOLD_ITALIC = re.compile(r"(\*\*\*|\*\*|\*|___|__|_)")
_HEADER = re.compile(r"^#{1,6}\s*", re.MULTILINE)
_BULLET = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_NUMBERED = re.compile(r"^\s*\d+\.\s+", re.MULTILINE)
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
# Anything that still reaches clean_for_speech has already been decided as
# NOT an internal thought (see _speak_unless_thought / _is_thought in
# voice/loop.py, which run on the raw sentence first) -- so a literal "("
# or ")" here is never a thought-marker worth preserving, only a stray
# character some TTS backends narrate literally ("open paren", or the
# parenthesis's own name). Confirmed live: this happened most on fragments
# left over from before the sentence-splitter was made paren-depth-aware
# (see _split_respecting_parens), but it's cheap insurance to keep even
# with that fixed -- an ordinary parenthetical aside like "the meeting is
# at 3pm (that's in 20 minutes)" should read as running speech, not have
# its punctuation spoken. Strip the bracket characters, keep the content.
_STRAY_BRACKETS = re.compile(r"[()\[\]]")
_WHITESPACE = re.compile(r"[ \t]+")
_BLANK_LINES = re.compile(r"\n{3,}")


def clean_for_speech(text: str) -> str:
    """Defensive backstop for TTS -- the system prompt asks the model not to
    use markdown, but this strips it anyway if it slips through, so it
    doesn't get read aloud literally (e.g. "asterisk asterisk bold text
    asterisk asterisk")."""
    text = _CODE_FENCE.sub("", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _LINK.sub(r"\1", text)
    text = _HEADER.sub("", text)
    text = _BULLET.sub("", text)
    text = _NUMBERED.sub("", text)
    text = _BOLD_ITALIC.sub("", text)
    text = _STRAY_BRACKETS.sub("", text)
    text = _WHITESPACE.sub(" ", text)
    text = _BLANK_LINES.sub("\n\n", text)
    return text.strip()
