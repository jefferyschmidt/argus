import logging
import re

from argus.llm.anthropic_client import AnthropicClient
from argus.llm.base import CompletionResult, Message, Tier
from argus.llm.cost_governor import BudgetExceeded, CostGovernor
from argus.llm.ollama_client import OllamaClient
from argus.llm.pricing import estimate_cost

log = logging.getLogger(__name__)

# Simple, editable heuristics for now. Swap for a learned classifier once
# there's usage data to tune it against.
_ADVANCED_KEYWORDS = (
    "plan",
    "design",
    "architecture",
    "debug",
    "analyze",
    "review",
    "refactor",
    "write code",
    "research",
)
_LOCAL_ENOUGH_MAX_LEN = 40
# LOCAL has no tool access (web search, filesystem, etc.), so it's reserved
# for genuine small talk -- anything that might need a real fact or current
# info defaults to FAST instead of guessing from length alone. This is a
# whitelist, not a blocklist, on purpose: default to giving tools access.
_SMALL_TALK_PATTERNS = re.compile(
    r"^(hi|hello|hey|yo|sup|good\s?(morning|afternoon|evening|night)|"
    r"how'?s?\s?it\s?going|how\s?are\s?you|what'?s\s?up|"
    r"thanks?|thank\s?you|thx|ok|okay|cool|nice|great|got\s?it|sounds?\s?good|"
    r"bye|goodbye|see\s?ya|later|good\s?bye)[.!? ]*$"
)


def classify(text: str) -> Tier:
    lowered = text.lower().strip()
    if any(kw in lowered for kw in _ADVANCED_KEYWORDS) or len(text) > 400:
        return Tier.ADVANCED
    if len(text) <= _LOCAL_ENOUGH_MAX_LEN and _SMALL_TALK_PATTERNS.match(lowered):
        return Tier.LOCAL
    return Tier.FAST


class ModelRouter:
    def __init__(self, daily_cap_usd: float = 5.0):
        self.local = OllamaClient()
        self.frontier = AnthropicClient()
        self.cost_governor = CostGovernor(daily_cap_usd=daily_cap_usd)
        if self.local.is_available():
            self.local.prewarm()

    def complete(
        self,
        messages: list[Message],
        system: str = "",
        force_tier: Tier | None = None,
    ) -> CompletionResult:
        tier = force_tier or classify(messages[-1].content if messages else "")

        if tier is Tier.LOCAL:
            if self.local.is_available():
                return self.local.complete(messages, system=system)
            log.warning("Ollama unavailable, escalating to frontier fast tier")
            tier = Tier.FAST

        try:
            self.cost_governor.check()
        except BudgetExceeded as e:
            log.warning(str(e))
            if self.local.is_available():
                return self.local.complete(messages, system=system)
            raise

        result = self.frontier.complete(messages, system=system, tier=tier)
        cost = estimate_cost(tier, result.input_tokens, result.output_tokens)
        self.cost_governor.record(cost)
        return result

    def complete_with_tools(
        self,
        user_text: str,
        system: str,
        tool_registry,
        force_tier: Tier | None = None,
    ) -> CompletionResult:
        """Tool use always runs on the frontier tier -- the local 3B model
        isn't reliable at structured tool calling, and if it's escalating
        to tools at all the task probably warranted the frontier anyway."""
        tier = force_tier or classify(user_text)
        if tier is Tier.LOCAL:
            tier = Tier.FAST

        self.cost_governor.check()  # let BudgetExceeded propagate -- no silent local fallback for tool use

        result = self.frontier.complete_with_tools(user_text, system, tool_registry, tier=tier)
        cost = estimate_cost(tier, result.input_tokens, result.output_tokens)
        self.cost_governor.record(cost)
        return result

    def complete_with_tools_streaming(
        self,
        user_text: str,
        system: str,
        tool_registry,
        on_text,
        force_tier: Tier | None = None,
    ) -> CompletionResult:
        tier = force_tier or classify(user_text)
        if tier is Tier.LOCAL:
            tier = Tier.FAST

        self.cost_governor.check()

        result = self.frontier.complete_with_tools_streaming(
            user_text, system, tool_registry, on_text, tier=tier
        )
        cost = estimate_cost(tier, result.input_tokens, result.output_tokens)
        self.cost_governor.record(cost)
        return result
