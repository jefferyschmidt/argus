import base64
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
over time); local filesystem/shell tools sandboxed to a workspace directory
plus the user's real Documents/Downloads/Desktop; and desktop control
(screenshot, list open windows, click, type, press keys, open apps) --
always take a screenshot first to see the actual screen before clicking or
typing, don't guess coordinates. Some tools require the user's explicit
confirmation before running -- if they decline, respect that and tell them
what you were trying to do instead of retrying.

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
confirmation."""


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

        reply, proposed = _extract_core_memory(result.text)
        if proposed:
            self.memory.core.propose(proposed)

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

        if classify(user_text) is Tier.LOCAL:
            # Ollama isn't streamed here -- local replies are short enough
            # that streaming wouldn't meaningfully reduce latency anyway.
            result = self.router.complete([Message(role="user", content=user_text)], system=system)
            self.last_tier = result.tier
            self.last_model = result.model
            reply, proposed = _extract_core_memory(result.text)
            if proposed:
                self.memory.core.propose(proposed)
            if reply:
                on_sentence(reply)
            self.memory.remember_turn("assistant", reply)
            ui_events.publish({"type": "transcript", "role": "argus", "text": reply, "tier": result.tier.value, "model": result.model})
            self._publish_turn_end()
            return reply

        buffer = SentenceBuffer()
        marker = "CORE_MEMORY:"

        def on_text(delta: str) -> None:
            for sentence in buffer.add(delta):
                if marker not in sentence:
                    on_sentence(sentence)

        result = self.router.complete_with_tools_streaming(
            user_text, system=system, tool_registry=self.tools, on_text=on_text, on_tool_call=self._on_tool_call
        )
        self.last_tier = result.tier
        self.last_model = result.model

        tail = buffer.flush()
        if tail and marker not in tail:
            on_sentence(tail)

        reply, proposed = _extract_core_memory(result.text)
        if proposed:
            self.memory.core.propose(proposed)

        self.memory.remember_turn("assistant", reply)
        ui_events.publish({"type": "transcript", "role": "argus", "text": reply, "tier": result.tier.value, "model": result.model})
        self._publish_turn_end()
        return reply


def _extract_core_memory(text: str) -> tuple[str, str | None]:
    marker = "CORE_MEMORY:"
    if marker not in text:
        return text, None
    body, _, tail = text.partition(marker)
    return body.strip(), tail.strip()
