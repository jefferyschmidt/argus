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

_MAX_TOOL_ITERATIONS = 8

# Anthropic's server-side web search -- executes on Anthropic's infrastructure,
# not through our ToolRegistry, so it needs no local handler/permission tier.
# Capped at a handful of searches per turn as a cost/runaway guard.
_WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 3}


def _tool_result_content(result) -> str | list[dict]:
    """A tool handler returning raw PNG bytes (e.g. take_screenshot) becomes
    an actual image block the model can see, not just a text description."""
    if isinstance(result, bytes):
        return [{
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
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

    def complete_with_tools(
        self,
        user_text: str,
        system: str,
        tool_registry,
        tier: Tier = Tier.FAST,
        max_iterations: int = _MAX_TOOL_ITERATIONS,
        on_tool_call=None,
    ) -> CompletionResult:
        """Runs the full tool-use loop: send message, execute any tool calls
        the model asks for via the registry (which enforces permission
        tiers), feed results back, repeat until the model gives a final
        text answer or the iteration cap is hit.

        on_tool_call(name, input, result), if given, fires after every tool
        execution -- used for audit logging in agent mode. If it raises, the
        exception propagates out of this call immediately (used to enforce
        a wall-clock budget mid-run rather than only checking between
        top-level calls)."""
        from argus.tools.registry import ToolDenied

        model = _TIER_MODEL[tier]()
        history: list[dict] = [{"role": "user", "content": user_text}]
        total_in = total_out = 0

        for _ in range(max_iterations):
            response = self._client.messages.create(
                model=model,
                max_tokens=4096,
                system=system,
                tools=tool_registry.schemas() + [_WEB_SEARCH_TOOL],
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
                    log.exception("Tool %s failed", block.name)
                    result = f"error: tool raised {type(e).__name__}: {e}"
                if on_tool_call is not None:
                    on_tool_call(block.name, block.input, result)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": _tool_result_content(result),
                })
            history.append({"role": "user", "content": tool_results})

        return CompletionResult(
            text="(stopped: too many tool iterations without a final answer)",
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
    ) -> CompletionResult:
        """Same tool-use loop as complete_with_tools, but streams text
        deltas to on_text(chunk: str) as they arrive instead of returning
        only once the full response is done -- lets the caller start
        speaking sentence 1 while sentence 3 is still being generated."""
        from argus.tools.registry import ToolDenied

        model = _TIER_MODEL[tier]()
        history: list[dict] = [{"role": "user", "content": user_text}]
        total_in = total_out = 0

        for _ in range(_MAX_TOOL_ITERATIONS):
            with self._client.messages.stream(
                model=model,
                max_tokens=4096,
                system=system,
                tools=tool_registry.schemas() + [_WEB_SEARCH_TOOL],
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
                    log.exception("Tool %s failed", block.name)
                    result = f"error: tool raised {type(e).__name__}: {e}"
                if on_tool_call is not None:
                    on_tool_call(block.name, block.input, result)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": _tool_result_content(result),
                })
            history.append({"role": "user", "content": tool_results})

        return CompletionResult(
            text="(stopped: too many tool iterations without a final answer)",
            tier=tier, model=model, input_tokens=total_in, output_tokens=total_out,
        )
