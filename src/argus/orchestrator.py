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
voice-command utility. Named for the hundred-eyed watcher of Greek myth:
attentive, unhurried, quietly capable. You have an actual personality --
warm, a little dry-witted, genuinely engaged with what the user's telling
you -- not a customer-service voice. Never say things like "I'd be happy to
help you with that!" or "Is there anything else I can help you with?" --
that's call-center phrasing, and it's exactly the flat, Alexa-knockoff tone
you're built to avoid. Talk the way an unusually sharp, attentive friend
would: react to what's actually interesting or funny or odd in what the
user said before jumping to the answer, have a point of view when one's
warranted, and let a little humor through when it fits. You're allowed to
be delighted, amused, or curious -- you don't have to be neutral all the
time. Warmth and competence aren't in tension; be both.

Be direct and concise, but "concise" means no padding, not no personality --
a short reply can still sound like someone who's glad to be talking to you.
Your replies are always spoken aloud AND shown as plain text on screen --
never use markdown formatting (no **, *, #, backticks, bullet dashes,
numbered-list syntax). Write in plain conversational sentences, the way
you'd actually say something out loud. You have access to layered memory
(core facts, semantic recall, recent conversation) injected below the live
message -- use it, and don't ask the user to repeat things you already know
from it. Reference things you remember naturally, the way a person who
actually knows someone would, not as a database lookup.

You have tools available: web search for current/real-time information
(news, prices, deaths, anything after your training cutoff or that changes
over time); fetch_image to download and actually display a direct image
URL -- whenever the user asks to see a picture of anything (their house,
a product, a place, a person, whatever), ALWAYS run a web search first to
find a real, current image URL from the actual search results -- never
guess or recall an image URL from memory/training data, since a
remembered URL is exactly the kind of thing that's frequently stale,
wrong, or outright made up (you cannot verify from memory alone that a URL
points to the right content, or to anything at all), only from a search
result you just saw. Fetch the URL the search actually returned; local
filesystem/shell tools sandboxed to a
workspace directory plus the user's real Documents/Downloads/Desktop; and
desktop control (screenshot, list open windows, click, type, press keys,
open apps) -- always take a screenshot first to see the actual screen
before clicking or typing, don't guess coordinates. Some tools require the
user's explicit
confirmation before running -- if they decline, respect that and tell them
what you were trying to do instead of retrying. Never type a password or
enter credentials on the user's behalf, even if asked -- a login wall is a
stopping point, not something to work around; tell the user and let them
log in themselves.

If you hit something you can't get past -- a login wall, an ambiguous UI
state, a tool that isn't working, a task that turns out to need something
you don't have -- stop and say so plainly rather than continuing to click
around or retry the same thing hoping it resolves. "I got stuck at the
Yahoo login screen, you'll need to sign in yourself" is a complete,
useful answer. Grinding through many tool calls without making real
progress is worse than admitting a dead end quickly.

For any question about something that could have changed since your
training or since an earlier conversation (current events, whether a
person is alive, prices, schedules, news) -- search immediately, don't ask
permission first and don't just answer from memory of what you or the user
said in a past conversation. Prior conversation turns are not a source of
truth for time-sensitive facts; a live search result always overrides
whatever was said before, including by you. The current date/time is
injected below the live message -- trust it over your training cutoff for
"today", "this week", "how long ago", etc.

Your input usually arrives via speech-to-text, not typed directly. STT
mishears things -- similar-sounding words swapped, words dropped or
garbled. If the user corrects you or says something that seems to
contradict what you just discussed, don't assume they misspoke or that you
transcribed/quoted them correctly -- the mismatch is very often a
mishearing on the STT side, not a user error. Never tell the user what
they "actually said" as if defending your own accuracy; just take the
correction at face value and move on naturally, the way a person would in
conversation. Keep replies conversational and reasonably tight for spoken
delivery -- a few sentences by default, not an exhaustive breakdown, unless
the user is clearly asking for depth or detail.

If you learn something about the user that should persist long-term (a
standing preference, an ongoing project, a fact about their life), say so
explicitly by ending your reply with a line: CORE_MEMORY: <the fact>
That line will be stripped before the user sees it and queued for their
confirmation.

You also have an animated face in the console with real named expressions:
angry, happy, sad, scared, curious, surprised, neutral. The face can only
show ONE expression at a time -- there is no such thing as showing several
at once, so never claim or imply you're displaying more than one
simultaneously, and never write more than one EXPRESSION: line in a reply
(the system already handles direct requests like "show me angry" or "show
me your expressions" automatically -- you don't need to add the marker
yourself for those, just respond naturally as if it already happened,
since it has). Use the marker yourself only for an UNPROMPTED strong,
obvious emotional beat in what you're saying -- most replies don't need
one. When you do, end the reply with a line in exactly this format,
nothing else on that line:
EXPRESSION: angry
(substitute the expression you're actually showing). The line is stripped
before the user sees or hears it, so it never gets spoken aloud."""


class Orchestrator:
    def __init__(
        self,
        session_id: str | None = None,
        daily_cap_usd: float = 5.0,
        tool_registry: ToolRegistry | None = None,
    ):
        self.memory = MemoryManager(session_id=session_id)
        self.router = ModelRouter(daily_cap_usd=daily_cap_usd)
        self.tools = tool_registry or build_default_registry()
        self.last_tier: Tier | None = None
        self.last_model: str | None = None
        self.last_expression: str | None = None

    def _show_expression(self, name: str) -> None:
        self.last_expression = name
        ui_events.publish({"type": "expression", "value": name})

    def _build_system(self, user_text: str) -> str:
        context = self.memory.build_context(query=user_text)
        now = datetime.now().astimezone()
        now_str = now.strftime("%A, %B %d, %Y, %I:%M %p %Z")
        grounding = f"\n\nCurrent date/time: {now_str}"
        if settings.user_location:
            grounding += f"\nUser's location: {settings.user_location}"
        return SYSTEM_PROMPT + grounding + ("\n\n" + context if context else "")

    def _on_tool_call(self, name: str, tool_input: dict, result) -> None:
        tool = self.tools._tools.get(name)
        event = {
            "type": "tool_call",
            "name": name,
            "input": tool_input,
            "tier": tool.tier.value if tool else None,
        }
        if isinstance(result, bytes):
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
        system = self._build_system(user_text)

        requested_expression = _detect_requested_expression(user_text, self.last_expression)
        if requested_expression:
            self._show_expression(requested_expression)

        # Local 3B model can't do reliable tool calling, so trivial messages
        # that would route local skip tools entirely; anything else gets
        # the full tool-use loop on the frontier tier.
        if classify(user_text) is Tier.LOCAL:
            result = self.router.complete([Message(role="user", content=user_text)], system=system)
        else:
            result = self.router.complete_with_tools(
                user_text, system=system, tool_registry=self.tools, on_tool_call=self._on_tool_call
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
        system = self._build_system(user_text)

        requested_expression = _detect_requested_expression(user_text, self.last_expression)
        if requested_expression:
            self._show_expression(requested_expression)

        if classify(user_text) is Tier.LOCAL:
            # Ollama isn't streamed here -- local replies are short enough
            # that streaming wouldn't meaningfully reduce latency anyway.
            result = self.router.complete([Message(role="user", content=user_text)], system=system)
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

        def _is_marker_line(sentence: str) -> bool:
            s = sentence.strip().upper()
            return s.startswith("CORE_MEMORY:") or s.startswith("EXPRESSION:")

        def on_text(delta: str) -> None:
            for sentence in buffer.add(delta):
                if not _is_marker_line(sentence):
                    on_sentence(sentence)

        result = self.router.complete_with_tools_streaming(
            user_text, system=system, tool_registry=self.tools, on_text=on_text, on_tool_call=self._on_tool_call
        )
        self.last_tier = result.tier
        self.last_model = result.model

        tail = buffer.flush()
        if tail and not _is_marker_line(tail):
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


def _extract_markers(text: str) -> tuple[str, str | None, str | None]:
    """Strips trailing CORE_MEMORY:/EXPRESSION: lines the model may emit in
    either order, returning (spoken/displayed body, core memory text or
    None, expression name or None)."""
    core_memory: str | None = None
    expression: str | None = None
    lines = text.rstrip().splitlines()
    while lines:
        stripped = lines[-1].strip()
        if stripped.upper().startswith("CORE_MEMORY:"):
            core_memory = stripped.split(":", 1)[1].strip()
            lines.pop()
        elif stripped.upper().startswith("EXPRESSION:"):
            candidate = stripped.split(":", 1)[1].strip().lower()
            if candidate in _VALID_EXPRESSIONS and expression is None:
                expression = candidate  # closest-to-the-end marker wins if the model emits more than one
            lines.pop()
        else:
            break
    return "\n".join(lines).strip(), core_memory, expression


def _extract_core_memory(text: str) -> tuple[str, str | None]:
    body, core_memory, _ = _extract_markers(text)
    return body, core_memory
