from argus.llm.base import Message, Tier
from argus.llm.router import ModelRouter, classify
from argus.memory.manager import MemoryManager
from argus.tools import ToolRegistry, build_default_registry

SYSTEM_PROMPT = """You are Argus, a personal AI assistant running locally for your user.
Be direct and concise. You have access to layered memory (core facts, semantic
recall, recent conversation) injected below the live message -- use it, and
don't ask the user to repeat things you already know from it.

You have tools available for interacting with the local filesystem and
shell, sandboxed to a workspace directory. Some tools require the user's
explicit confirmation before running -- if they decline, respect that and
tell them what you were trying to do instead of retrying.

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

    def handle(self, user_text: str) -> str:
        self.memory.remember_turn("user", user_text)

        context = self.memory.build_context(query=user_text)
        system = SYSTEM_PROMPT + ("\n\n" + context if context else "")

        # Local 3B model can't do reliable tool calling, so trivial messages
        # that would route local skip tools entirely; anything else gets
        # the full tool-use loop on the frontier tier.
        if classify(user_text) is Tier.LOCAL:
            result = self.router.complete([Message(role="user", content=user_text)], system=system)
        else:
            result = self.router.complete_with_tools(user_text, system=system, tool_registry=self.tools)

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
