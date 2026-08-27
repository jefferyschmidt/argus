import logging

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
_LOCAL_ENOUGH_MAX_LEN = 60


def classify(text: str) -> Tier:
    lowered = text.lower()
    if any(kw in lowered for kw in _ADVANCED_KEYWORDS) or len(text) > 400:
        return Tier.ADVANCED
    if len(text) <= _LOCAL_ENOUGH_MAX_LEN:
        return Tier.LOCAL
    return Tier.FAST


class ModelRouter:
    def __init__(self, daily_cap_usd: float = 5.0):
        self.local = OllamaClient()
        self.frontier = AnthropicClient()
        self.cost_governor = CostGovernor(daily_cap_usd=daily_cap_usd)

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
