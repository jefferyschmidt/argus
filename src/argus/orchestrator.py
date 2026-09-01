import base64
import re
from datetime import datetime

from argus.config import settings
from argus.llm.base import Message, Tier
from argus.llm.router import ModelRouter, classify
from argus.memory.manager import MemoryManager
from argus.persona import CONVERSATION_PROMPT, SYSTEM_PROMPT
from argus.tools import ToolRegistry, build_default_registry
from argus.ui import events as ui_events
from argus.voice.sentence_splitter import SentenceBuffer

# Spoken chat has no tools and shouldn't be allowed to ramble into a
# monologue -- max_tokens is a hard cap the old "40 words" prompt rule
# never actually was. Tool-using turns keep the higher budget.
_CHAT_MAX_TOKENS = 120
_CHAT_EXPANDED_MAX_TOKENS = 220
_EXPANDED_CHAT = re.compile(
    r"\b(explain|why|how|compare|explore|think through|tell me about|"
    r"write|brainstorm|advice|help me understand|deep dive)\b",
    re.IGNORECASE,
)
_RELATIVE_TIME_MEMORY = re.compile(
    r"\b(today|tomorrow|yesterday|tonight|this\s+(morning|afternoon|evening|week|month|year)|"
    r"next\s+(week|month|year)|last\s+(week|month|year))\b",
    re.IGNORECASE,
)
_EXPLICIT_RECALL = re.compile(
    r"\b(remember|remind me|what did (i|we) (say|talk about)|"
    r"have (i|we) talked about|you know about me|my preference)\b",
    re.IGNORECASE,
)
_TOOL_ACTION = re.compile(
    r"\b(open|close|launch|start|stop|send|write|create|save|delete|move|copy|"
    r"search|look up|check|show|take|capture|scan|set|turn on|turn off|"
    r"schedule|remind|book|download|upload|run|list|read|find)\b",
    re.IGNORECASE,
)
_TOOL_SUBJECT = re.compile(
    r"\b(weather|forecast|email|inbox|mail|calendar|reminder|timer|alarm|"
    r"screenshot|camera|photo|screen|browser|website|file|folder|document|"
    r"pdf|app|window|volume|news|price|stock|search|google|chrome|firefox|"
    r"calculator|notepad|spotify|explorer)\b",
    re.IGNORECASE,
)
_DIRECT_TOOL_INTENT = re.compile(
    r"^(remind me\b|set (?:a )?(?:reminder|timer|alarm)\b|"
    r"what time is it\b|tell me (?:the )?(?:weather|forecast|news|score)\b)",
    re.IGNORECASE,
)


def _is_durable_core_memory_candidate(text: str) -> bool:
    """Relative-time facts rot immediately once placed in always-on memory.
    They require an absolute date or belong in episodic memory instead."""
    return bool(text.strip()) and not _RELATIVE_TIME_MEMORY.search(text)


def _chat_max_tokens(user_text: str) -> int:
    """Keep spoken small talk tight while leaving room for an explicitly
    exploratory or substantial conversation."""
    if len(user_text) > 220 or _EXPANDED_CHAT.search(user_text):
        return _CHAT_EXPANDED_MAX_TOKENS
    return _CHAT_MAX_TOKENS


def _should_use_tools(user_text: str) -> bool:
    """Keep a mention of a tool-domain in the conversational lane.

    The old keyword-only routing treated "what do you think about my camera"
    like a camera command, injecting the whole operational prompt and tool
    schema. Tools need both an action and a concrete operational subject,
    with a few factual queries handled as direct requests.
    """
    text = user_text.strip()
    factual_query = re.match(r"^(what(?:'s| is) (?:the )?(weather|forecast|news|score|price)|when is )", text, re.I)
    return bool(_DIRECT_TOOL_INTENT.search(text) or factual_query or (_TOOL_ACTION.search(text) and _TOOL_SUBJECT.search(text)))


class Orchestrator:
    def __init__(
        self,
        session_id: str | None = None,
        daily_cap_usd: float = 5.0,
        tool_registry: ToolRegistry | None = None,
    ):
        self.memory = MemoryManager(session_id=session_id)
        self.router = ModelRouter(daily_cap_usd=daily_cap_usd)

        # Phase I autonomous tasks (PRD §6), off by default
        # (enable_task_runner). Exposed as self.task_runner (not just
        # closed over inside the tool registry) so ProactiveEngine --
        # constructed from this orchestrator, after it -- can reach the
        # same instance for startup reconciliation rather than building a
        # second one (P4). Its own SpineStore: ProactiveEngine's doesn't
        # exist yet at this point in construction, and two independent
        # SpineStore instances to the same file are safe under WAL (each
        # guards its own connection with its own lock, PRD §5.2's
        # generalized P1 rule) -- a minor, documented exception to P4
        # forced by that ordering, not a duplicated-and-unsynchronized
        # connection.
        self.task_runner = None
        if settings.enable_task_runner:
            from argus.spine.store import SpineStore
            from argus.tasks.store import TaskStore
            from argus.tasks.worker import TaskRunner
            self.task_runner = TaskRunner(TaskStore(), SpineStore(), self.router)

        self.tools = tool_registry or build_default_registry(router=self.router, task_runner=self.task_runner)
        self.last_tier: Tier | None = None
        self.last_model: str | None = None
        self.last_expression: str | None = None

        # Lets UI-server endpoints that need their own LLM call (e.g. idle
        # emote generation) reach the SAME router this conversation uses,
        # not a second untracked one -- see ui/commands.py's active-router
        # registry for why that matters (shared cost governor).
        from argus.ui import commands as ui_commands
        ui_commands.set_active_router(self.router)
        ui_commands.set_active_memory_manager(self.memory)

    def _show_expression(self, name: str) -> None:
        self.last_expression = name
        ui_events.publish({"type": "expression", "value": name})

    def _build_dynamic_system(self, user_text: str) -> str:
        """The part of the system prompt that changes every turn (current
        time down to the minute, recalled memory context) -- split out from
        SYSTEM_PROMPT so callers that support prompt caching (the frontier
        tool-use path) can cache the large, genuinely-static instructions
        block separately and only resend this small dynamic suffix fresh
        each time. See AnthropicClient._system_param for why concatenating
        them back into one string would defeat caching entirely.

        Recent turns are NOT injected here -- they go in as real chat
        messages (see dialogue_messages) so the model treats them as a
        conversation rather than a briefing document."""
        context = self.memory.build_context(query=user_text, include_recent=False)
        now = datetime.now().astimezone()
        now_str = now.strftime("%A, %B %d, %Y, %I:%M %p %Z")
        grounding = f"\n\nCurrent date/time: {now_str}"
        if settings.user_location:
            grounding += f"\nUser's location: {settings.user_location}"
        return grounding + ("\n\n" + context if context else "")

    def _chat_messages(self, user_text: str) -> list[Message]:
        return self.memory.dialogue_messages() + [Message(role="user", content=user_text)]

    def _chat_system(self, user_text: str) -> str:
        """Chat uses Argus's conversational persona plus the small dynamic
        context. It intentionally excludes the large tool-use manual: that
        instruction set makes a simple exchange read like a support flow,
        and the persona is below the provider's cacheable-prompt minimum."""
        context = self.memory.build_conversation_context(
            user_text, include_recall=bool(_EXPLICIT_RECALL.search(user_text))
        )
        return CONVERSATION_PROMPT.rstrip() + ("\n\n" + context if context else "")

    def _on_tool_call(self, name: str, tool_input: dict, result, tokens_used: int = 0) -> None:
        # tokens_used (cumulative input+output tokens through this call) is
        # unused here -- normal chat has no token budget to enforce, only
        # agent/runner.py's autonomous runs do. Accepted so this callback
        # doesn't break now that AnthropicClient passes it to every
        # on_tool_call, agent mode or not.
        tool = self.tools._tools.get(name)
        event = {
            "type": "tool_call",
            "name": name,
            "input": tool_input,
            "tier": tool.tier.value if tool else None,
        }
        if isinstance(result, bytes):
            if name == "capture_camera":
                # capture_camera publishes its own display event (a
                # stylized rendering by default, the raw frame only when
                # explicitly asked -- see desktop.py's _capture_camera) --
                # skip the generic auto-display here, or the raw photo
                # would ALSO silently show up, defeating that default.
                event["result"] = f"<{len(result)} bytes>"
            else:
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
        if not _is_durable_core_memory_candidate(text):
            return
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

    def _finish_turn(self, result, requested_expression: str | None) -> str:
        self.last_tier = result.tier
        self.last_model = result.model
        reply, proposed, expression = _extract_markers(result.text)
        if proposed:
            self._propose_core_memory(proposed)
        if expression and not requested_expression:
            self._show_expression(expression)
        self.memory.remember_turn("assistant", reply)
        ui_events.publish({
            "type": "transcript", "role": "argus", "text": reply,
            "tier": result.tier.value, "model": result.model,
        })
        self._publish_turn_end()
        return reply

    def handle(self, user_text: str) -> str:
        ui_events.publish({"type": "transcript", "role": "you", "text": user_text})
        ui_events.publish({"type": "state", "value": "thinking"})
        self.memory.remember_turn("user", user_text)
        self.tools.reset_task_autonomy(explicitly_requested=_should_use_tools(user_text))

        requested_expression = _detect_requested_expression(user_text, self.last_expression)
        if requested_expression:
            self._show_expression(requested_expression)

        # Chat-shaped turns skip the tool schema entirely and stay on the
        # cheap frontier model (Haiku) -- same voice as tool-using turns,
        # without paying to re-prime fifty tools or dumping the reply onto
        # the local 3B/gpt-oss slot that can't actually hold a conversation.
        # The personality prompt is deliberately below Anthropic's prompt-
        # caching minimum, so send it normally rather than turning small
        # talk into an invalid API request.
        if not _should_use_tools(user_text):
            result = self.router.complete(
                self._chat_messages(user_text),
                system=self._chat_system(user_text),
                force_tier=Tier.ADVANCED if classify(user_text) is Tier.ADVANCED else Tier.FAST,
                max_tokens=_chat_max_tokens(user_text),
            )
        else:
            result = self.router.complete_with_tools(
                user_text, system=self._build_dynamic_system(user_text), tool_registry=self.tools,
                on_tool_call=self._on_tool_call, cacheable_system=SYSTEM_PROMPT,
                prior_messages=self.memory.dialogue_messages(),
            )

        return self._finish_turn(result, requested_expression)

    def handle_streaming(self, user_text: str, on_sentence) -> str:
        """Like handle(), but calls on_sentence(text) as each complete
        sentence becomes available instead of only returning once the full
        reply is done -- lets voice mode start speaking sentence 1 while
        later sentences are still being generated."""
        ui_events.publish({"type": "transcript", "role": "you", "text": user_text})
        ui_events.publish({"type": "state", "value": "thinking"})
        self.memory.remember_turn("user", user_text)
        self.tools.reset_task_autonomy(explicitly_requested=_should_use_tools(user_text))

        requested_expression = _detect_requested_expression(user_text, self.last_expression)
        if requested_expression:
            self._show_expression(requested_expression)

        buffer = SentenceBuffer()

        def _strip_marker_lines(sentence: str) -> str:
            # Strips a marker even if it's not the sentence's ENTIRE
            # content -- the sentence splitter can bundle a marker together
            # with surrounding text when the model doesn't cleanly newline-
            # separate it (observed live: "EXPRESSION: angry" got spoken
            # aloud because it arrived glued to other text as one chunk).
            kept = [line for line in sentence.splitlines() if not _MARKER_LINE.match(line)]
            return "\n".join(kept).strip()

        def on_text(delta: str) -> None:
            for sentence in buffer.add(delta):
                cleaned = _strip_marker_lines(sentence)
                if cleaned:
                    on_sentence(cleaned)

        if not _should_use_tools(user_text):
            result = self.router.complete_streaming(
                self._chat_messages(user_text),
                system=self._chat_system(user_text),
                on_text=on_text,
                force_tier=Tier.ADVANCED if classify(user_text) is Tier.ADVANCED else Tier.FAST,
                max_tokens=_chat_max_tokens(user_text),
            )
        else:
            result = self.router.complete_with_tools_streaming(
                user_text, system=self._build_dynamic_system(user_text), tool_registry=self.tools,
                on_text=on_text, on_tool_call=self._on_tool_call, cacheable_system=SYSTEM_PROMPT,
                prior_messages=self.memory.dialogue_messages(),
            )

        # `or ""` matters: flush() returns None when the reply happened to
        # end exactly on a sentence boundary (a final delta ending in
        # ". "), which left the buffer empty. _strip_marker_lines(None)
        # then raised AttributeError, and the whole turn died into
        # _process_utterance's "Something went wrong on that one" handler.
        tail = _strip_marker_lines(buffer.flush() or "")
        if tail:
            on_sentence(tail)

        return self._finish_turn(result, requested_expression)


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


_MARKER_LINE = re.compile(r"^\s*(CORE_MEMORY|EXPRESSION)\s*:\s*(.*)$", re.IGNORECASE)


def _extract_markers(text: str) -> tuple[str, str | None, str | None]:
    """Strips CORE_MEMORY:/EXPRESSION: lines wherever they appear in the
    reply, not just at the very end -- the prompt asks the model to only
    ever put them last, but a fast/small model doesn't reliably follow
    that (observed live: it kept talking after an EXPRESSION: line, which
    a trailing-only stripper never reaches, so the raw tag leaked out as
    visible/spoken text). Returns (body with all marker lines removed,
    core memory text or None, expression name or None -- last valid match
    wins if the model emits more than one of either)."""
    core_memory: str | None = None
    expression: str | None = None
    body_lines: list[str] = []
    for line in text.splitlines():
        match = _MARKER_LINE.match(line)
        if not match:
            body_lines.append(line)
            continue
        kind, value = match.group(1).upper(), match.group(2).strip()
        if kind == "CORE_MEMORY" and value:
            core_memory = value
        elif kind == "EXPRESSION" and value.lower() in _VALID_EXPRESSIONS:
            expression = value.lower()
    return "\n".join(body_lines).strip(), core_memory, expression
