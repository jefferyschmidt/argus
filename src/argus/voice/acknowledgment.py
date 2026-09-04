"""PRD.md §15 unit 32. Deterministic, no LLM in the loop: a completed
transcript matching a small explicit acknowledgment phrase list, arriving
within settings.acknowledgment_window_seconds of the last thing Argus
actually SPOKE (not held, not ambient -- see SalienceDispatcher) about a
thread, closes that one thread -- via ProactiveEngine.acknowledge_thread,
the exact same mechanism the dashboard's "got it" button already uses
(unit 30). Only the single most recently spoken-about thread is ever
eligible; never a bulk close, never an older one.

Shared by both voice loops (voice/loop.py, voice/realtime.py) so they
don't grow separate phrase lists or separate window logic. PRD.md §19
unit 40 Part 2: is_voice_confirmation_active() is now also checked
INSIDE this function, not just at each call site -- both loops
previously each carried their own copy of that guard (realtime's via
an if/elif branch structure that only reaches this call once the
confirmation-diversion branch has already been ruled out; pipeline's
via an explicit standalone check, deliberately redundant against
future callers per its own "belt-and-braces" comment, since neither
loop's structural guarantee that this is never reached mid-confirmation
is itself enforced anywhere). Both loops' own checks are left in place
(a caller re-checking something this function also checks is harmless,
and pipeline's own comment explains why it wants that defense in
depth) -- but the canonical rule now lives here too, once, so a future
third caller can't forget it."""

import time

from argus.config import settings
from argus.ui import commands as ui_commands

# Deliberately NOT voice/confirm.py's _YES_WORDS: a bare "yes" answering
# some unrelated question must never silently close a thread. Closing a
# thread the user didn't mean to close loses the item, so this errs
# toward not firing.
ACKNOWLEDGMENT_PHRASES = ("got it", "thanks", "noted", "handled", "dealt with it", "took care of it")


def is_acknowledgment_phrase(text: str) -> bool:
    lowered = text.strip().lower()
    return any(phrase in lowered for phrase in ACKNOWLEDGMENT_PHRASES)


def maybe_acknowledge_spoken_thread(text: str, proactive_engine, now: float | None = None) -> bool:
    """Returns True iff this transcript actually closed a thread. A
    non-match, no recently-spoken thread, or an expired window are all
    the same "do nothing" outcome from the caller's point of view -- this
    is a side effect on an otherwise normal transcript, never something
    that should suppress or replace the rest of that turn's handling
    (a compound utterance like "got it, also email Bob" must still reach
    the model as a real request).

    `proactive_engine` may be None -- both voice loops construct one
    unconditionally in __init__, but plenty of existing tests exercise
    _process_utterance()/_receive() on a deliberately partial instance
    that skips it, same as other optional collaborators elsewhere in
    this codebase (see registry.py's getattr(tool, "group", None))."""
    if proactive_engine is None:
        return False
    if ui_commands.is_voice_confirmation_active():
        return False  # a yes/no meant for a pending confirmer must never close a thread here
    if not is_acknowledgment_phrase(text):
        return False

    dispatcher = proactive_engine.dispatcher
    thread_id = dispatcher.last_spoken_thread_id
    spoken_ts = dispatcher.last_spoken_ts
    if thread_id is None or spoken_ts is None:
        return False

    now = now if now is not None else time.time()
    if now - spoken_ts > settings.acknowledgment_window_seconds:
        return False

    return proactive_engine.acknowledge_thread(thread_id, via="voice")
