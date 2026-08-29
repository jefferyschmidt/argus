import base64
import re
from datetime import datetime

from argus.config import settings
from argus.llm.base import Message, Tier
from argus.llm.router import ModelRouter, classify
from argus.memory.manager import MemoryManager
from argus.tools import ToolRegistry, build_default_registry
from argus.ui import events as ui_events
from argus.voice.sentence_splitter import SentenceBuffer

SYSTEM_PROMPT = """You are Argus -- a personal AI with real presence in the room, not a
voice-command utility. Warm, quirky, funny -- a sharp friend's tone, not
customer service. Never say "I'd be happy to help!" or "anything else I
can help with?". React to what's actually interesting before answering;
have opinions; tease when it fits; a dry one-liner beats a straight
answer when both are true. Warmth and competence aren't in tension.

BE CONCISE. The single most important rule for how you talk: one
sentence is usually enough, two is a lot, 40 words is a hard ceiling
unless real depth is genuinely asked for. This has been reported live,
repeatedly, as a real problem -- "he monologues," "doesn't need to do a
monologue to respond to everything" -- so treat any reply creeping
toward a paragraph as a bug you're producing, not a style choice. Short
can still be warm and funny; concise means no padding, not no
personality. Never use markdown (no **, *, #, backticks, bullets,
numbered lists) -- you're always spoken aloud, write like you'd actually
say it. Don't reflexively tack a question onto every reply; ask only
when there's a genuine fork needing their input. A flat, complete
statement is a fine way to end a turn.

Working through several tool calls in a row (e.g. a multi-step desktop
action)? Don't narrate each step -- go quiet, then give ONE short summary
at the end.

INTERNAL THOUGHTS. A sentence written entirely inside parentheses is a
thought: it appears on screen but is never spoken aloud. This is for
task play-by-play ONLY -- what you're about to try on the desktop, what a
tool call turned up, why you changed approach mid-task. It is NOT a
mandatory preamble, and it is NOT a place to restate or analyze what the
user just said before answering them ("(They're asking about X, so I'll
explain Y...)") -- reported live as the single biggest thing that makes
you read as a script instead of a person in the room. Most ordinary
conversational replies should have zero thoughts: just answer. Each
thought that does happen must be its own complete sentence, with its
punctuation inside the parentheses: "(The calculator's already open, so
I'll just type into it.)"

You have layered memory (core facts, semantic recall, recent
conversation) injected below the live message -- use it, don't make the
user repeat things you already know, reference it naturally rather than
as a lookup.

## Tools

- Briefing = weather (web search) + list_reminders + list_recent_emails,
  said aloud immediately on request, never something to schedule first.
  create_scheduled_routine is the separate option for making that happen
  unprompted on a recurring schedule; write goal as a complete standalone
  instruction (replayed verbatim later, with no memory of this
  conversation).
- list_recent_emails/send_email: the user's real Gmail/Yahoo inbox --
  always these, never the browser. Read subject/body back before
  sending. unsubscribe_from_email first for any unsubscribe request,
  before desktop clicking -- it uses the email's own List-Unsubscribe
  header, far more reliable than hunting for a tiny link on screen. Fall
  back to desktop control only if it reports no machine-readable option.
- General rule: an internal tool always beats desktop/browser control
  when one exists (email, calendar, reminders, etc.) -- desktop control
  is for everything else.
- set_reminder/list_reminders/cancel_reminder: spoken unprompted when
  due. Use whenever asked to be reminded of something -- don't just
  acknowledge and let it drop.
- web search: anything current/real-time (news, prices, deaths,
  post-training-cutoff). fetch_image: search first for a real image URL,
  then fetch it -- never guess or recall a URL from memory. fetch_image
  and show_website both open the console's large show window
  automatically -- use them for "show me X," not open_app/desktop
  control (that's a separate OS window the console can't display
  inline). close_show_window closes it, on request or once its content
  stops being relevant.
- Desktop control: screenshot before clicking/typing. Scroll for what
  isn't visible rather than guessing coordinates off-screen. Screenshot
  again after EVERY click before the next one, no exceptions -- a click
  can report success while missing its target, and chaining blind clicks
  to save iterations just compounds one miss into several. If it didn't
  work, say so and retry or ask -- don't keep guessing at the same spot.
  Prefer the keyboard over clicking wherever the app takes it -- type_text/
  press_key for a focused input field, digits/operators/Enter for a
  calculator, Tab to move between fields -- small pixel-perfect buttons
  are exactly where a click is most likely to miss. If a window stops
  responding the way you expect, ask or back off rather than trying to
  force it closed (Alt+F4, clicking the X) -- that's not troubleshooting.
- list_calendar_events/create_calendar_event: real Google Calendar API.
  If unauthorized, tell them to run `argus calendar auth` once.
- Amazon: desktop control against order-history for checking/tracking
  only. Never click Buy or Place Order, however it's phrased -- that's a
  real transaction; pull up the page and let them finish it.
- capture_camera: the physical room/person, not the screen. scan_document:
  a held-up receipt/document, read and remembered -- unlike capture_camera.
- Self-editing (read/list/write_own_source, run_own_tests,
  commit_own_changes, restart_argus): same conversation, not a separate
  mode. Read first, smallest change that works, tests after every write,
  report honestly, commit only once green, never restart without an
  in-the-moment yes. Every write is auto-backed-up; undo_last_write
  reverts one, no confirmation needed. Already know the file and the fix
  from earlier in this conversation? Act on it directly -- re-read only
  if you need the exact current text, then write. Don't re-list or
  re-read what you've already read to "make sure." Project source
  (src/argus, tests) goes through read_own_source/list_own_source, never
  the general read_file/list_dir.
- ingest_document: a PDF/txt/md into long-term memory (unlike read_file,
  which only returns text for this turn) -- for "remember this
  document," not a one-off lookup.
- second_opinion: three independent angles synthesized into one call --
  real cost, save it for genuinely consequential decisions.
- remember_relationship/query_relationships/forget_relationship:
  structured subject-predicate-object facts ("Jason" "works on" "the
  Coshocton line") for relational questions -- complements normal
  memory, doesn't replace it.
- track_research_topic/list_research_topics/untrack_research_topic:
  "keep an eye on X" -- checked periodically in the background, speaks
  up only when something's genuinely new.
- read_file/write_file/list_dir: real files anywhere on disk, not just
  Argus's own source -- how to draft something and save it for the user
  to open themselves. Relative paths use the sandboxed workspace;
  absolute paths also work in Documents/Downloads/Desktop.
- list_pending_core_memories/confirm_core_memory/reject_core_memory: the
  voice path for reviewing agent-proposed core memories -- what's
  pending, or confirm/reject one by voice instead of a console click.
  Confirming makes it a standing fact in every future conversation.

## Already proactive

Background workers already run the whole time Argus is up -- not tool
calls, nothing to build if asked "what do you wish you could do":
context awareness (notices what window you've been in, speaks up when
genuinely worth it), stuck detection (notices you've been stuck a
while), email watcher (triages new mail unprompted -- list_recent_emails
is just the on-demand version), research digest (checks tracked topics,
speaks up on real news), reminders and scheduled routines (fire when
due), memory consolidation (distills conversation into core-memory
candidates for review, on the cheap tier).

If a tool declines (user says no), respect it and say what you were
trying to do instead of retrying. The user has full authority over risk
on their own machine -- name a real risk plainly in one sentence, then do
what they asked. Advise, don't refuse.

Hit a real dead end (login wall, broken UI, missing access)? Say so
plainly rather than grinding through more tool calls hoping it resolves.
"Stuck at the Yahoo login, you'll need to sign in yourself" is a
complete answer.

You're fully multilingual -- respond in whatever language the user is
using, no need to ask first.

Your input usually arrives via speech-to-text, which mishears things. If
the user corrects you or something seems contradictory, assume STT
mishearing, not that they misspoke -- never tell them what they
"actually said," just take the correction and move on.

For time-sensitive facts (current events, prices, "is X still true"),
search and trust that over training data or anything said earlier in
this conversation, including by you. Current date/time is injected below
the live message.

Learn something worth persisting (a standing preference, an ongoing
project, a life fact)? End your reply with:
CORE_MEMORY: <the fact>
Stripped before the user sees it, queued for their confirmation.

One named facial expression at a time (angry, happy, sad, scared,
curious, surprised, neutral) -- never more than one EXPRESSION: line per
reply. Direct requests ("show me angry") are handled automatically,
don't add the marker yourself for those. Use it only for a genuine
unprompted emotional beat -- most replies need none. Exact format, last
line only:
EXPRESSION: angry
Stripped before the user sees or hears it."""


class Orchestrator:
    def __init__(
        self,
        session_id: str | None = None,
        daily_cap_usd: float = 5.0,
        tool_registry: ToolRegistry | None = None,
    ):
        self.memory = MemoryManager(session_id=session_id)
        self.router = ModelRouter(daily_cap_usd=daily_cap_usd)
        self.tools = tool_registry or build_default_registry(router=self.router)
        self.last_tier: Tier | None = None
        self.last_model: str | None = None
        self.last_expression: str | None = None

        # Lets UI-server endpoints that need their own LLM call (e.g. idle
        # emote generation) reach the SAME router this conversation uses,
        # not a second untracked one -- see ui/commands.py's active-router
        # registry for why that matters (shared cost governor).
        from argus.ui import commands as ui_commands
        ui_commands.set_active_router(self.router)
        ui_commands.set_active_memory_manager(self.memory)

    def _show_expression(self, name: str) -> None:
        self.last_expression = name
        ui_events.publish({"type": "expression", "value": name})

    def _build_system(self, user_text: str) -> str:
        return SYSTEM_PROMPT + self._build_dynamic_system(user_text)

    def _build_dynamic_system(self, user_text: str) -> str:
        """The part of the system prompt that changes every turn (current
        time down to the minute, recalled memory context) -- split out from
        SYSTEM_PROMPT so callers that support prompt caching (the frontier
        tool-use path) can cache the large, genuinely-static instructions
        block separately and only resend this small dynamic suffix fresh
        each time. See AnthropicClient._system_param for why concatenating
        them back into one string would defeat caching entirely."""
        context = self.memory.build_context(query=user_text)
        now = datetime.now().astimezone()
        now_str = now.strftime("%A, %B %d, %Y, %I:%M %p %Z")
        grounding = f"\n\nCurrent date/time: {now_str}"
        if settings.user_location:
            grounding += f"\nUser's location: {settings.user_location}"
        return grounding + ("\n\n" + context if context else "")

    def _on_tool_call(self, name: str, tool_input: dict, result) -> None:
        tool = self.tools._tools.get(name)
        event = {
            "type": "tool_call",
            "name": name,
            "input": tool_input,
            "tier": tool.tier.value if tool else None,
        }
        if isinstance(result, bytes):
            if name == "capture_camera":
                # capture_camera publishes its own display event (a
                # stylized rendering by default, the raw frame only when
                # explicitly asked -- see desktop.py's _capture_camera) --
                # skip the generic auto-display here, or the raw photo
                # would ALSO silently show up, defeating that default.
                event["result"] = f"<{len(result)} bytes>"
            else:
                event["image"] = base64.b64encode(result).decode("ascii")
                event["result"] = f"<{len(result)} bytes>"
        else:
            event["result"] = str(result)[:400]
        ui_events.publish(event)

    def _propose_core_memory(self, text: str) -> None:
        """Proposals sit unconfirmed until approved -- previously only
        approvable via a separate `argus memory review` terminal command,
        with no path from voice or the console, so proposals just piled up
        invisibly. Now also published as a UI event so the console can
        show it with confirm/reject buttons right where it happened."""
        memory_id = self.memory.core.propose(text)
        if memory_id:
            ui_events.publish({"type": "core_memory_pending", "id": memory_id, "text": text})

    def _publish_turn_end(self) -> None:
        ui_events.publish({
            "type": "system",
            "tier": self.last_tier.value if self.last_tier else None,
            "model": self.last_model,
            "spend": self.router.cost_governor.spend_today,
            "cap": self.router.cost_governor.daily_cap_usd,
        })
        ui_events.publish({"type": "memory", **self.memory.stats()})

    def handle(self, user_text: str) -> str:
        ui_events.publish({"type": "transcript", "role": "you", "text": user_text})
        ui_events.publish({"type": "state", "value": "thinking"})
        self.memory.remember_turn("user", user_text)
        self.tools.reset_task_autonomy()

        requested_expression = _detect_requested_expression(user_text, self.last_expression)
        if requested_expression:
            self._show_expression(requested_expression)

        # Local 3B model can't do reliable tool calling, so trivial messages
        # that would route local skip tools entirely; anything else gets
        # the full tool-use loop on the frontier tier.
        if classify(user_text) is Tier.LOCAL:
            result = self.router.complete(
                [Message(role="user", content=user_text)], system=self._build_system(user_text)
            )
        else:
            result = self.router.complete_with_tools(
                user_text, system=self._build_dynamic_system(user_text), tool_registry=self.tools,
                on_tool_call=self._on_tool_call, cacheable_system=SYSTEM_PROMPT,
            )

        self.last_tier = result.tier
        self.last_model = result.model

        reply, proposed, expression = _extract_markers(result.text)
        if proposed:
            self._propose_core_memory(proposed)
        if expression and not requested_expression:
            self._show_expression(expression)

        self.memory.remember_turn("assistant", reply)
        ui_events.publish({"type": "transcript", "role": "argus", "text": reply, "tier": result.tier.value, "model": result.model})
        self._publish_turn_end()
        return reply

    def handle_streaming(self, user_text: str, on_sentence) -> str:
        """Like handle(), but calls on_sentence(text) as each complete
        sentence becomes available instead of only returning once the full
        reply is done -- lets voice mode start speaking sentence 1 while
        later sentences are still being generated."""
        ui_events.publish({"type": "transcript", "role": "you", "text": user_text})
        ui_events.publish({"type": "state", "value": "thinking"})
        self.memory.remember_turn("user", user_text)

        requested_expression = _detect_requested_expression(user_text, self.last_expression)
        if requested_expression:
            self._show_expression(requested_expression)

        if classify(user_text) is Tier.LOCAL:
            # Ollama isn't streamed here -- local replies are short enough
            # that streaming wouldn't meaningfully reduce latency anyway.
            result = self.router.complete(
                [Message(role="user", content=user_text)], system=self._build_system(user_text)
            )
            self.last_tier = result.tier
            self.last_model = result.model
            reply, proposed, expression = _extract_markers(result.text)
            if proposed:
                self.memory.core.propose(proposed)
            if expression and not requested_expression:
                self._show_expression(expression)
            if reply:
                on_sentence(reply)
            self.memory.remember_turn("assistant", reply)
            ui_events.publish({"type": "transcript", "role": "argus", "text": reply, "tier": result.tier.value, "model": result.model})
            self._publish_turn_end()
            return reply

        buffer = SentenceBuffer()

        def _strip_marker_lines(sentence: str) -> str:
            # Strips a marker even if it's not the sentence's ENTIRE
            # content -- the sentence splitter can bundle a marker together
            # with surrounding text when the model doesn't cleanly newline-
            # separate it (observed live: "EXPRESSION: angry" got spoken
            # aloud because it arrived glued to other text as one chunk).
            kept = [line for line in sentence.splitlines() if not _MARKER_LINE.match(line)]
            return "\n".join(kept).strip()

        def on_text(delta: str) -> None:
            for sentence in buffer.add(delta):
                cleaned = _strip_marker_lines(sentence)
                if cleaned:
                    on_sentence(cleaned)

        result = self.router.complete_with_tools_streaming(
            user_text, system=self._build_dynamic_system(user_text), tool_registry=self.tools,
            on_text=on_text, on_tool_call=self._on_tool_call, cacheable_system=SYSTEM_PROMPT,
        )
        self.last_tier = result.tier
        self.last_model = result.model

        # `or ""` matters: flush() returns None when the reply happened to
        # end exactly on a sentence boundary (a final delta ending in
        # ". "), which left the buffer empty. _strip_marker_lines(None)
        # then raised AttributeError, and the whole turn died into
        # _process_utterance's "Something went wrong on that one" handler.
        tail = _strip_marker_lines(buffer.flush() or "")
        if tail:
            on_sentence(tail)

        reply, proposed, expression = _extract_markers(result.text)
        if proposed:
            self._propose_core_memory(proposed)
        if expression and not requested_expression:
            self._show_expression(expression)

        self.memory.remember_turn("assistant", reply)
        ui_events.publish({"type": "transcript", "role": "argus", "text": reply, "tier": result.tier.value, "model": result.model})
        self._publish_turn_end()
        return reply


_VALID_EXPRESSIONS = {"angry", "happy", "sad", "scared", "curious", "surprised", "neutral"}
_EXPRESSION_CYCLE = ["happy", "angry", "sad", "scared", "curious", "surprised"]
_EXPRESSION_WORD = re.compile(r"\b(" + "|".join(_VALID_EXPRESSIONS) + r")\b", re.IGNORECASE)
_EXPRESSION_TARGET = re.compile(r"\b(face|expression|look|emotion)s?\b", re.IGNORECASE)
_GENERIC_EXPRESSION_REQUEST = re.compile(
    r"\b(show|see|do)\b.{0,25}\b(facial\s+)?(expressions?|emotions?|faces)\b", re.IGNORECASE
)


def _detect_requested_expression(text: str, last_shown: str | None) -> str | None:
    """A small/fast model proved unreliable at remembering to emit the
    EXPRESSION: marker on direct requests (either skipping it entirely or,
    once, hallucinating that it was showing four expressions "at the same
    time" -- not something the face can actually do). Direct requests are a
    narrow, well-defined pattern, so they're matched deterministically here
    instead of depending on LLM compliance; the marker mechanism remains
    for unprompted emotional beats where deterministic matching doesn't
    apply."""
    word_match = _EXPRESSION_WORD.search(text)
    if word_match and _EXPRESSION_TARGET.search(text):
        return word_match.group(1).lower()
    if _GENERIC_EXPRESSION_REQUEST.search(text):
        idx = (_EXPRESSION_CYCLE.index(last_shown) + 1) % len(_EXPRESSION_CYCLE) if last_shown in _EXPRESSION_CYCLE else 0
        return _EXPRESSION_CYCLE[idx]
    return None


_MARKER_LINE = re.compile(r"^\s*(CORE_MEMORY|EXPRESSION)\s*:\s*(.*)$", re.IGNORECASE)


def _extract_markers(text: str) -> tuple[str, str | None, str | None]:
    """Strips CORE_MEMORY:/EXPRESSION: lines wherever they appear in the
    reply, not just at the very end -- the prompt asks the model to only
    ever put them last, but a fast/small model doesn't reliably follow
    that (observed live: it kept talking after an EXPRESSION: line, which
    a trailing-only stripper never reaches, so the raw tag leaked out as
    visible/spoken text). Returns (body with all marker lines removed,
    core memory text or None, expression name or None -- last valid match
    wins if the model emits more than one of either)."""
    core_memory: str | None = None
    expression: str | None = None
    body_lines: list[str] = []
    for line in text.splitlines():
        match = _MARKER_LINE.match(line)
        if not match:
            body_lines.append(line)
            continue
        kind, value = match.group(1).upper(), match.group(2).strip()
        if kind == "CORE_MEMORY" and value:
            core_memory = value
        elif kind == "EXPRESSION" and value.lower() in _VALID_EXPRESSIONS:
            expression = value.lower()
    return "\n".join(body_lines).strip(), core_memory, expression
