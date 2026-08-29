import base64
import logging

import anthropic

from argus.config import settings
from argus.llm.base import CompletionResult, Message, Tier

log = logging.getLogger(__name__)

_TIER_MODEL = {
    Tier.FAST: lambda: settings.anthropic_model,
    Tier.ADVANCED: lambda: settings.anthropic_advanced_model,
}

# Confirmed live as a real, self-defeating bug: 8 was too tight for a
# genuine multi-step desktop task done properly (screenshot, click,
# screenshot again to verify, repeat -- e.g. "open the calculator and add
# 4+4" needs open_app + a screenshot/click/verify pair per button, at
# least 8-9 calls before even reaching "="). Hit this cap mid-task three
# times in one session, and each time forced a tradeoff between
# completing the task and verifying each click -- observed live as
# clicking the same coordinates twice and chaining several clicks between
# screenshots (skipping the system prompt's own "screenshot after every
# click" instruction) specifically to cram more actions into too few
# iterations, then still running out before finishing. 20 gives enough
# room for a real verify-every-click desktop workflow without being an
# open-ended agent loop (see argus/agent/runner.py's separate, much
# higher cap for that).
_MAX_TOOL_ITERATIONS = 20

# Anthropic's server-side web search -- executes on Anthropic's infrastructure,
# not through our ToolRegistry, so it needs no local handler/permission tier.
# Capped at a handful of searches per turn as a cost/runaway guard.
_WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 3}


def _system_param(system: str, cacheable_system: str):
    """Builds the `system` kwarg for a messages.create call. When
    cacheable_system is given, splits it into its own cached content block
    (cache_control: ephemeral) ahead of the per-turn dynamic system text,
    instead of sending one concatenated string -- caching requires an exact
    prefix match, and the dynamic part (current date/time down to the
    minute, recalled memory context) changes on essentially every call, so
    caching the whole string together would never hit. The static
    instructions (SYSTEM_PROMPT) are the same on every turn within a
    session and, as of this codebase's tool count, comfortably over
    Anthropic's minimum cacheable size -- splitting them out means only the
    first call in a session pays full input-token processing for them."""
    if not cacheable_system:
        return system or anthropic.NOT_GIVEN
    blocks = [{"type": "text", "text": cacheable_system, "cache_control": {"type": "ephemeral"}}]
    if system:
        blocks.append({"type": "text", "text": system})
    return blocks


def _cached_tools(tool_registry) -> list[dict]:
    """Tool definitions are identical across calls within a run (the
    registry doesn't change mid-session) and, with this codebase's current
    tool count, add real bulk to every request -- worth its own cache
    breakpoint on the last block, which caches everything up to and
    including it (the whole tools array), separate from the system-prompt
    breakpoint above."""
    tools = tool_registry.schemas() + [_WEB_SEARCH_TOOL]
    tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}
    return tools


def _image_media_type(data: bytes) -> str:
    """Sniffs the real format from magic bytes rather than assuming PNG.
    Confirmed live as a real, crashing bug: take_screenshot returns PNG
    (pyautogui) but capture_camera returns JPEG (cv2.imencode(".jpg", ...))
    -- both used to be sent through _tool_result_content hardcoded to
    "image/png", which the Anthropic API flatly rejects on a mismatch
    (400 Bad Request), and because this path has no try/except around it,
    that exception propagated all the way up and killed the whole voice
    process rather than degrading gracefully."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    return "image/png"


def _tool_result_content(result) -> str | list[dict]:
    """A tool handler returning raw image bytes (e.g. take_screenshot,
    capture_camera) becomes an actual image block the model can see, not
    just a text description."""
    if isinstance(result, bytes):
        return [{
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": _image_media_type(result),
                "data": base64.b64encode(result).decode("ascii"),
            },
        }]
    return str(result)


class AnthropicClient:
    """Frontier tier. Does the real reasoning since local generation is
    CPU-bound on this machine."""

    def __init__(self):
        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    def complete(
        self, messages: list[Message], system: str = "", tier: Tier = Tier.FAST
    ) -> CompletionResult:
        model = _TIER_MODEL[tier]()
        response = self._client.messages.create(
            model=model,
            max_tokens=4096,
            system=system or anthropic.NOT_GIVEN,
            messages=[{"role": m.role, "content": m.content} for m in messages],
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        return CompletionResult(
            text=text,
            tier=tier,
            model=model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )

    def complete_with_image(
        self, image_bytes: bytes, prompt: str, tier: Tier = Tier.FAST, media_type: str = "image/jpeg"
    ) -> CompletionResult:
        """One-shot vision call, no tool loop -- for tools that already have
        an image in hand (e.g. scan_document) and just need it looked at
        and described/extracted, not a multi-turn conversation."""
        model = _TIER_MODEL[tier]()
        response = self._client.messages.create(
            model=model,
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": base64.b64encode(image_bytes).decode("ascii")},
                    },
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        text = "".join(b.text for b in response.content if b.type == "text")
        return CompletionResult(
            text=text, tier=tier, model=model,
            input_tokens=response.usage.input_tokens, output_tokens=response.usage.output_tokens,
        )

    def complete_with_tools(
        self,
        user_text: str,
        system: str,
        tool_registry,
        tier: Tier = Tier.FAST,
        max_iterations: int = _MAX_TOOL_ITERATIONS,
        on_tool_call=None,
        cacheable_system: str = "",
    ) -> CompletionResult:
        """Runs the full tool-use loop: send message, execute any tool calls
        the model asks for via the registry (which enforces permission
        tiers), feed results back, repeat until the model gives a final
        text answer or the iteration cap is hit.

        on_tool_call(name, input, result), if given, fires after every tool
        execution -- used for audit logging in agent mode. If it raises, the
        exception propagates out of this call immediately (used to enforce
        a wall-clock budget mid-run rather than only checking between
        top-level calls).

        cacheable_system, if given, is the stable part of the system prompt
        (see _system_param) -- callers with a per-turn-varying system
        string (current time, recalled memory) should pass that as `system`
        and the static instructions separately here, or caching never hits."""
        from argus.tools.registry import ToolDenied

        model = _TIER_MODEL[tier]()
        history: list[dict] = [{"role": "user", "content": user_text}]
        total_in = total_out = 0
        tools = _cached_tools(tool_registry)

        for _ in range(max_iterations):
            response = self._client.messages.create(
                model=model,
                max_tokens=4096,
                system=_system_param(system, cacheable_system),
                tools=tools,
                messages=history,
            )
            total_in += response.usage.input_tokens
            total_out += response.usage.output_tokens

            if response.stop_reason != "tool_use":
                text = "".join(b.text for b in response.content if b.type == "text")
                return CompletionResult(
                    text=text, tier=tier, model=model,
                    input_tokens=total_in, output_tokens=total_out,
                )

            history.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                try:
                    result = tool_registry.execute(block.name, block.input)
                except ToolDenied as e:
                    result = f"error: {e}"
                except Exception as e:
                    # Not log.exception -- tool errors are fed back to the
                    # model and it usually self-corrects (e.g. malformed
                    # arguments, retried with the right ones), so a full
                    # traceback on every recoverable hiccup is just noise.
                    log.warning("Tool %s failed: %s: %s", block.name, type(e).__name__, e)
                    result = f"error: tool raised {type(e).__name__}: {e}"
                if on_tool_call is not None:
                    on_tool_call(block.name, block.input, result)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": _tool_result_content(result),
                })
            history.append({"role": "user", "content": tool_results})

        # Confirmed live as a real bug: this used to be
        # "(stopped: too many tool iterations without a final answer)" --
        # fully wrapped in parens, which made _is_thought (voice/loop.py)
        # classify it as an internal thought and silently swallow it. A
        # desktop-automation task (deleting emails via webmail clicking)
        # hit this exact cutoff after 20+ iterations and the user got zero
        # feedback that it had failed -- not even a caption, just silence.
        # Plain sentence text, no wrapping parens, so it's spoken like any
        # other reply.
        return CompletionResult(
            text="I got stuck working through that and had to stop -- want me to try again?",
            tier=tier, model=model, input_tokens=total_in, output_tokens=total_out,
        )

    def complete_with_tools_streaming(
        self,
        user_text: str,
        system: str,
        tool_registry,
        on_text,
        tier: Tier = Tier.FAST,
        on_tool_call=None,
        cacheable_system: str = "",
    ) -> CompletionResult:
        """Same tool-use loop as complete_with_tools, but streams text
        deltas to on_text(chunk: str) as they arrive instead of returning
        only once the full response is done -- lets the caller start
        speaking sentence 1 while sentence 3 is still being generated.

        cacheable_system: see complete_with_tools."""
        from argus.tools.registry import ToolDenied

        model = _TIER_MODEL[tier]()
        history: list[dict] = [{"role": "user", "content": user_text}]
        total_in = total_out = 0
        tools = _cached_tools(tool_registry)

        for _ in range(_MAX_TOOL_ITERATIONS):
            with self._client.messages.stream(
                model=model,
                max_tokens=4096,
                system=_system_param(system, cacheable_system),
                tools=tools,
                messages=history,
            ) as stream:
                for text in stream.text_stream:
                    on_text(text)
                response = stream.get_final_message()

            total_in += response.usage.input_tokens
            total_out += response.usage.output_tokens

            if response.stop_reason != "tool_use":
                text = "".join(b.text for b in response.content if b.type == "text")
                return CompletionResult(
                    text=text, tier=tier, model=model,
                    input_tokens=total_in, output_tokens=total_out,
                )

            history.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                try:
                    result = tool_registry.execute(block.name, block.input)
                except ToolDenied as e:
                    result = f"error: {e}"
                except Exception as e:
                    # Not log.exception -- tool errors are fed back to the
                    # model and it usually self-corrects (e.g. malformed
                    # arguments, retried with the right ones), so a full
                    # traceback on every recoverable hiccup is just noise.
                    log.warning("Tool %s failed: %s: %s", block.name, type(e).__name__, e)
                    result = f"error: tool raised {type(e).__name__}: {e}"
                if on_tool_call is not None:
                    on_tool_call(block.name, block.input, result)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": _tool_result_content(result),
                })
            history.append({"role": "user", "content": tool_results})

        # Confirmed live as a real bug: this used to be
        # "(stopped: too many tool iterations without a final answer)" --
        # fully wrapped in parens, which made _is_thought (voice/loop.py)
        # classify it as an internal thought and silently swallow it. A
        # desktop-automation task (deleting emails via webmail clicking)
        # hit this exact cutoff after 20+ iterations and the user got zero
        # feedback that it had failed -- not even a caption, just silence.
        # Plain sentence text, no wrapping parens, so it's spoken like any
        # other reply.
        return CompletionResult(
            text="I got stuck working through that and had to stop -- want me to try again?",
            tier=tier, model=model, input_tokens=total_in, output_tokens=total_out,
        )
