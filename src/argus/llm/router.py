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
# LOCAL means "conversation, not a job" -- the orchestrator still runs
# these on the cheap frontier model (Haiku, no tool schema), not the
# 3B/gpt-oss slot. Whitelist on purpose: anything that might need a
# real fact or an action defaults to FAST.
_SMALL_TALK_PATTERNS = re.compile(
    r"^(hi|hello|hey|yo|sup|good\s?(morning|afternoon|evening|night)|"
    r"how'?s?\s?it\s?going|how\s?are\s?you|what'?s\s?up|"
    r"thanks?|thank\s?you|thx|ok|okay|cool|nice|great|got\s?it|sounds?\s?good|"
    r"bye|goodbye|see\s?ya|later|good\s?bye)[.!? ]*$"
)
_BACKCHANNEL = re.compile(
    r"^(yeah|yep|yup|nah|nope|ok(ay)?|cool|nice|wow|oof|damn|true|fair|"
    r"same|lol|lmao|haha+|heh|huh|m+hm+|right|sure|exactly|totally|"
    r"interesting|wild|crazy|same here|i know|fair enough|"
    r"no worries|all good|ha|oh)[.!? ]*$"
)
_CHATTY = re.compile(
    r"\b(how are you|how'?re you feeling|how is it going|"
    r"who are you|what are you|tell me a joke|make me laugh|roast me|"
    r"be honest|what do you (think|make of that)|your (take|opinion)|"
    r"talk to me|tell me about yourself|"
    r"i('m| am) (bored|lonely|tired|fried|exhausted|sad|happy|good)|"
    r"you (suck|rock|rule)|that'?s (funny|hilarious|dumb|fair|true)|"
    r"you'?re (the worst|the best|annoying|funny|a riot))\b"
)
_NEEDS_TOOLS = re.compile(
    r"\b(weather|forecast|email|inbox|gmail|yahoo|mail|remind|calendar|"
    r"schedule|search|look\s*up|google|news|price|stock|screenshot|"
    r"click|send|delete|camera|photo|website|fetch|download|commit|"
    r"restart|undo|pdf|document|scan|amazon|translate|timer|alarm|"
    r"quiet mode|show me|open (the |my )?(app|chrome|firefox|calculator|"
    r"browser|file|folder))\b"
    r"|what'?s (the )?(weather|score|news|forecast)"
)


def classify(text: str) -> Tier:
    lowered = text.lower().strip()
    if any(kw in lowered for kw in _ADVANCED_KEYWORDS) or len(text) > 400:
        return Tier.ADVANCED
    if _NEEDS_TOOLS.search(lowered):
        return Tier.FAST
    if _SMALL_TALK_PATTERNS.match(lowered) or _BACKCHANNEL.match(lowered) or _CHATTY.search(lowered):
        return Tier.LOCAL
    return Tier.FAST


_OFFLINE_NO_LOCAL_MESSAGE = (
    "I'm completely offline right now -- no internet reachable, and no local "
    "model either. I can't help with anything until one of those is back."
)


def _can_degrade_frontier_error(error: anthropic.APIError) -> bool:
    """Only degrade errors that mean the frontier service is unavailable.
    Invalid requests must still surface during development instead of being
    hidden behind an unrelated local-model reply."""
    if isinstance(error, anthropic.APIConnectionError):
        return True
    detail = str(error).lower()
    return "credit balance is too low" in detail or "insufficient credit" in detail


def _frontier_unavailable_note(error: anthropic.APIError, no_tools: bool = False) -> str:
    note = "offline -- no internet" if isinstance(error, anthropic.APIConnectionError) else "online model unavailable"
    return f"{note}, no tools/web access" if no_tools else note


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
        max_tokens: int | None = None,
        cacheable_system: str = "",
    ) -> CompletionResult:
        tier = force_tier or classify(messages[-1].content if messages else "")
        local_system = "\n\n".join(part for part in (cacheable_system, system) if part)

        if tier is Tier.LOCAL:
            if self.local.is_available():
                try:
                    return self.local.complete(messages, system=local_system)
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
                        return self.local.complete(messages, system=local_system)
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
                return self.local.complete(messages, system=local_system)
            raise

        try:
            kwargs = {"cacheable_system": cacheable_system}
            if max_tokens is not None:
                kwargs["max_tokens"] = max_tokens
            result = self.frontier.complete(messages, system=system, tier=tier, **kwargs)
        except anthropic.APIError as e:
            if not _can_degrade_frontier_error(e):
                raise
            log.warning("Anthropic unavailable (%s); degrading to offline fallback", type(e).__name__)
            return self._degraded_result(
                messages[-1].content if messages else "", local_system, _frontier_unavailable_note(e)
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

    def complete_streaming(
        self,
        messages: list[Message],
        system: str,
        on_text,
        force_tier: Tier | None = None,
        max_tokens: int | None = None,
        cacheable_system: str = "",
    ) -> CompletionResult:
        """Streams a chat reply. Local clients do not expose streaming, so
        their completed response is delivered as one chunk instead."""
        tier = force_tier or classify(messages[-1].content if messages else "")
        local_system = "\n\n".join(part for part in (cacheable_system, system) if part)

        if tier is Tier.LOCAL:
            if self.local.is_available():
                try:
                    result = self.local.complete(messages, system=local_system)
                    on_text(result.text)
                    return result
                except Exception:
                    log.exception("Local streaming completion failed; escalating")
            tier = Tier.FAST

        self.cost_governor.check()
        kwargs = {"cacheable_system": cacheable_system}
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        try:
            result = self.frontier.complete_streaming(
                messages, system=system, on_text=on_text, tier=tier, **kwargs
            )
        except anthropic.APIError as e:
            if not _can_degrade_frontier_error(e):
                raise
            log.warning("Anthropic unavailable (%s); degrading to offline fallback", type(e).__name__)
            result = self._degraded_result(
                messages[-1].content if messages else "", local_system, _frontier_unavailable_note(e)
            )
            on_text(result.text)
            return result
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
        prior_messages: list[Message] | None = None,
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
        if prior_messages:
            kwargs["prior_messages"] = prior_messages

        try:
            result = self.frontier.complete_with_tools(user_text, system, tool_registry, tier=tier, **kwargs)
        except anthropic.APIError as e:
            if not _can_degrade_frontier_error(e):
                raise
            log.warning("Anthropic unavailable (%s); degrading to local fallback (no tools/web access)", type(e).__name__)
            return self._degraded_result(user_text, system, _frontier_unavailable_note(e, no_tools=True))
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
        prior_messages: list[Message] | None = None,
    ) -> CompletionResult:
        """cacheable_system: see complete_with_tools."""
        tier = force_tier or classify(user_text)
        if tier is Tier.LOCAL:
            tier = Tier.FAST

        self.cost_governor.check()

        try:
            kwargs = {"cacheable_system": cacheable_system}
            if prior_messages:
                kwargs["prior_messages"] = prior_messages
            result = self.frontier.complete_with_tools_streaming(
                user_text, system, tool_registry, on_text, tier=tier, on_tool_call=on_tool_call,
                **kwargs,
            )
        except anthropic.APIError as e:
            if not _can_degrade_frontier_error(e):
                raise
            log.warning("Anthropic unavailable (%s); degrading to local fallback (no tools/web access)", type(e).__name__)
            result = self._degraded_result(user_text, system, _frontier_unavailable_note(e, no_tools=True))
            # Handles the common case (can't connect at all, so nothing was
            # streamed yet) correctly. A connection drop mid-response after
            # some real text already streamed is a rarer case this doesn't
            # fully solve -- the fallback text would follow the partial one.
            on_text(result.text)
            return result
        cost = estimate_cost(tier, result.input_tokens, result.output_tokens)
        self.cost_governor.record(cost)
        return result
