"""PRD.md §19 unit 40 Part 2. The transcript+caption event pair every
spoken-output path (dispatcher delivery, escalation follow-ups, both
voice loops' own acknowledgement/error/prompt speech) publishes --
previously written out inline at each call site, in both voice loops
and salience/dispatch.py, with no single place a new delivery path
could be relied on to also get right. EscalationScheduler.process_due()
was exactly that: it bypassed SalienceDispatcher._deliver() entirely
and so never published anything, which is why pipeline mode never
captioned an escalation follow-up (found at the §19 u41/u40-Part-1
pipeline-harness gate, closed here)."""

from argus.ui import events as ui_events

# Every transcript event, in both loops, already always carried an
# explicit role -- but caption events historically didn't (pipeline
# mode never included one; realtime always did). The UI treats a
# missing role on a caption identically to role="argus" (see
# ui/static/index.html's addCaption/role handling), so this is not an
# observable rendering difference either way -- but preserving each
# call site's exact original event shape byte-for-byte, rather than
# normalizing it, is the more conservative reading of "don't change
# what either mode does observably" for something this test suite
# already asserts the literal shape of (test_internal_thoughts.py).
# _OMIT (not None -- a caller might conceivably want role=None
# preserved literally) marks "don't include this key at all."
_OMIT = object()


def publish_transcript(text: str, role: str = "argus") -> None:
    ui_events.publish({"type": "transcript", "role": role, "text": text})


def publish_caption(text: str, role=_OMIT) -> None:
    event = {"type": "caption", "text": text}
    if role is not _OMIT:
        event["role"] = role
    ui_events.publish(event)


def publish_spoken(text: str, role=_OMIT, *, transcript: bool = True) -> None:
    """The common case: both events, in order. transcript=False
    publishes only the caption -- matches the per-sentence streaming
    case (VoiceLoop._speak_unless_thought), where the full transcript is
    published separately, once, when the whole reply is known, not per
    sentence. A caller needing some other combination (RealtimeVoiceLoop
    ._receive's response.done handling always publishes the transcript
    but only conditionally the caption, to avoid double-captioning a
    reply already captioned incrementally) composes publish_transcript/
    publish_caption directly instead of forcing an awkward parameter
    combination onto this one.

    role defaults to _OMIT (not "argus") for the caption half
    specifically, matching the historical majority shape across both
    loops' plain-reply captions; the transcript half still always gets
    an explicit role ("argus" unless the caller passes "you"), since
    every transcript event always had one."""
    transcript_role = "argus" if role is _OMIT else role
    if transcript:
        publish_transcript(text, transcript_role)
    publish_caption(text, role)
