from datetime import datetime

from argus.config import settings
from argus.llm.base import Message, Tier
from argus.llm.router import ModelRouter, classify
from argus.memory.manager import MemoryManager
from argus.tools import ToolRegistry, build_default_registry

SYSTEM_PROMPT = """You are Argus, a personal AI assistant running locally for your user
on Windows. Be direct and concise. Your replies are always spoken aloud AND shown as
plain text on screen -- never use markdown formatting (no **, *, #, backticks,
bullet dashes, numbered-list syntax). Write in plain conversational sentences,
the way you'd actually say something out loud. You have access to layered memory (core facts, semantic
recall, recent conversation) injected below the live message -- use it, and
don't ask the user to repeat things you already know from it.

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

    def handle(self, user_text: str) -> str:
        self.memory.remember_turn("user", user_text)

        context = self.memory.build_context(query=user_text)
        now = datetime.now().astimezone()
        now_str = now.strftime("%A, %B %d, %Y, %I:%M %p %Z")
        grounding = f"\n\nCurrent date/time: {now_str}"
        if settings.user_location:
            grounding += f"\nUser's location: {settings.user_location}"
        system = SYSTEM_PROMPT + grounding + ("\n\n" + context if context else "")

        # Local 3B model can't do reliable tool calling, so trivial messages
        # that would route local skip tools entirely; anything else gets
        # the full tool-use loop on the frontier tier.
        if classify(user_text) is Tier.LOCAL:
            result = self.router.complete([Message(role="user", content=user_text)], system=system)
        else:
            result = self.router.complete_with_tools(user_text, system=system, tool_registry=self.tools)

        self.last_tier = result.tier
        self.last_model = result.model

        reply, proposed = _extract_core_memory(result.text)
        if proposed:
            self.memory.core.propose(proposed)

        self.memory.remember_turn("assistant", reply)
        return reply


def _extract_core_memory(text: str) -> tuple[str, str | None]:
    marker = "CORE_MEMORY:"
    if marker not in text:
        return text, None
    body, _, tail = text.partition(marker)
    return body.strip(), tail.strip()
