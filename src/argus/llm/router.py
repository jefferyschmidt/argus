import logging
import re
import time

import anthropic
import groq

from argus.config import settings
from argus.llm.anthropic_client import AnthropicClient
from argus.llm.base import CompletionResult, Message, Tier
from argus.llm.cost_governor import BudgetExceeded, CostGovernor
from argus.llm.groq_client import GroqClient
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


_OFFLINE_NO_LOCAL_MESSAGE = (
    "I'm completely offline right now -- no internet reachable, and no local "
    "model either. I can't help with anything until one of those is back."
)


class ModelRouter:
    def __init__(self, daily_cap_usd: float = 5.0):
        # Groq (hosted, no cold start) replaces Ollama in this low-latency
        # slot when configured -- Ollama's CPU-bound cold start on this
        # hardware was the actual bottleneck. Kept as OllamaClient with no
        # key set so a from-scratch install with no GROQ_API_KEY still
        # works exactly as before.
        self.local = GroqClient() if settings.groq_api_key else OllamaClient()
        # Always a REAL local model, regardless of what self.local is --
        # this is specifically for the case where there's no internet at
        # all, which Groq (hosted) can't help with the way genuine
        # on-device inference can. Reuses self.local's own instance when
        # it's already Ollama rather than opening a second connection.
        self.offline_fallback = self.local if isinstance(self.local, OllamaClient) else OllamaClient()
        self.frontier = AnthropicClient()
        self.cost_governor = CostGovernor(daily_cap_usd=daily_cap_usd)
        if isinstance(self.local, OllamaClient) and self.local.is_available():
            self.local.prewarm()

    def _degraded_result(self, user_text: str, system: str, note: str) -> CompletionResult:
        """The frontier tier (network) is unreachable. Rather than let the
        exception propagate and crash the whole turn -- previously the only
        behavior, since nothing here caught APIConnectionError -- fall back
        to a genuinely local model if it's up (with a clear disclaimer that
        tools/web access aren't available in this fallback), or return a
        plain "I'm offline" message if even that's unreachable. Uses
        offline_fallback specifically, not self.local -- if self.local is
        Groq, it needs internet too and would be just as unreachable as
        Anthropic during a real outage. Either way the caller always gets
        back a normal CompletionResult, never an exception, so the rest of
        the turn (memory, transcript, TTS) keeps working instead of dying
        mid-conversation."""
        if self.offline_fallback.is_available():
            try:
                result = self.offline_fallback.complete(
                    [Message(role="user", content=user_text)], system=system
                )
                result.text = f"({note}) {result.text}"
                return result
            except Exception:
                log.exception("Local fallback also failed during offline degradation")
        return CompletionResult(text=_OFFLINE_NO_LOCAL_MESSAGE, tier=Tier.LOCAL, model="offline")

    def complete(
        self,
        messages: list[Message],
        system: str = "",
        force_tier: Tier | None = None,
    ) -> CompletionResult:
        tier = force_tier or classify(messages[-1].content if messages else "")

        if tier is Tier.LOCAL:
            if self.local.is_available():
                try:
                    return self.local.complete(messages, system=system)
                except groq.RateLimitError:
                    # Confirmed live: Groq's free tier is a tight shared
                    # budget (8000 TPM) across every LOCAL-tier caller --
                    # idle emotes, memory consolidation, small talk -- and
                    # its own error message routinely says "try again in
                    # ~360ms." Escalating straight to the paid frontier
                    # tier on the very first rate-limit hit defeats the
                    # entire point of the free local tier for what's
                    # usually a very short-lived contention spike. One
                    # quick retry after a short pause avoids that cost in
                    # the common case; if it's still rate-limited, the
                    # existing escalation below is still the safety net.
                    log.warning("Local tier rate-limited; retrying once before escalating")
                    time.sleep(1.5)
                    try:
                        return self.local.complete(messages, system=system)
                    except Exception:
                        log.exception("Local completion still failing after rate-limit retry; escalating")
                except Exception:
                    # is_available() can be stale (Ollama was up a moment
                    # ago, isn't now) -- don't let that raise all the way
                    # out, just escalate the same as if it were never
                    # available. If frontier is ALSO unreachable from here,
                    # the try/except below catches that and degrades too.
                    log.exception("Local completion failed despite is_available()==True; escalating")
            else:
                log.warning("Ollama unavailable, escalating to frontier fast tier")
            tier = Tier.FAST

        try:
            self.cost_governor.check()
        except BudgetExceeded as e:
            log.warning(str(e))
            if self.local.is_available():
                return self.local.complete(messages, system=system)
            raise

        try:
            result = self.frontier.complete(messages, system=system, tier=tier)
        except anthropic.APIConnectionError:
            log.warning("Anthropic API unreachable; degrading to offline fallback")
            return self._degraded_result(
                messages[-1].content if messages else "", system, "offline -- no internet"
            )
        cost = estimate_cost(tier, result.input_tokens, result.output_tokens)
        self.cost_governor.record(cost)
        return result

    def complete_with_image(
        self, image_bytes: bytes, prompt: str, tier: Tier = Tier.FAST, media_type: str = "image/jpeg"
    ) -> CompletionResult:
        """No local/offline path -- vision needs the frontier model, so this
        always costs real money and always needs internet, unlike complete()
        which degrades gracefully. Callers (e.g. scan_document) should
        already be behind a CONFIRM-tier tool for the same reason
        capture_camera is."""
        self.cost_governor.check()
        result = self.frontier.complete_with_image(image_bytes, prompt, tier=tier, media_type=media_type)
        cost = estimate_cost(tier, result.input_tokens, result.output_tokens)
        self.cost_governor.record(cost)
        return result

    def complete_with_tools(
        self,
        user_text: str,
        system: str,
        tool_registry,
        force_tier: Tier | None = None,
        max_iterations: int | None = None,
        on_tool_call=None,
        cacheable_system: str = "",
    ) -> CompletionResult:
        """Tool use always runs on the frontier tier -- the local 3B model
        isn't reliable at structured tool calling, and if it's escalating
        to tools at all the task probably warranted the frontier anyway.

        cacheable_system: the stable part of the system prompt, split out
        for Anthropic prompt caching -- see AnthropicClient.complete_with_tools."""
        tier = force_tier or classify(user_text)
        if tier is Tier.LOCAL:
            tier = Tier.FAST

        self.cost_governor.check()  # let BudgetExceeded propagate -- no silent local fallback for tool use

        kwargs = {}
        if max_iterations is not None:
            kwargs["max_iterations"] = max_iterations
        if on_tool_call is not None:
            kwargs["on_tool_call"] = on_tool_call
        if cacheable_system:
            kwargs["cacheable_system"] = cacheable_system

        try:
            result = self.frontier.complete_with_tools(user_text, system, tool_registry, tier=tier, **kwargs)
        except anthropic.APIConnectionError:
            log.warning("Anthropic API unreachable; degrading to offline fallback (no tools/web access)")
            return self._degraded_result(user_text, system, "offline -- no internet, no tools/web access")
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
        on_tool_call=None,
        cacheable_system: str = "",
    ) -> CompletionResult:
        """cacheable_system: see complete_with_tools."""
        tier = force_tier or classify(user_text)
        if tier is Tier.LOCAL:
            tier = Tier.FAST

        self.cost_governor.check()

        try:
            result = self.frontier.complete_with_tools_streaming(
                user_text, system, tool_registry, on_text, tier=tier, on_tool_call=on_tool_call,
                cacheable_system=cacheable_system,
            )
        except anthropic.APIConnectionError:
            log.warning("Anthropic API unreachable; degrading to offline fallback (no tools/web access)")
            result = self._degraded_result(user_text, system, "offline -- no internet, no tools/web access")
            # Handles the common case (can't connect at all, so nothing was
            # streamed yet) correctly. A connection drop mid-response after
            # some real text already streamed is a rarer case this doesn't
            # fully solve -- the fallback text would follow the partial one.
            on_text(result.text)
            return result
        cost = estimate_cost(tier, result.input_tokens, result.output_tokens)
        self.cost_governor.record(cost)
        return result
