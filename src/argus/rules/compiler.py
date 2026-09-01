"""PRD.md §7.2. Authoring-time only -- one of the handful of places
PRD §0's non-negotiable rules permit an LLM call at all. Reuses
CoreMemoryStore's propose/confirm precedent (memory/core.py): compile()
only ever proposes a rule; a human confirms separately via
RuleStore.confirm(). Never activates on its own."""

import json
import logging
import re
from dataclasses import dataclass, field

from argus.llm.base import Message, Tier
from argus.rules.store import RuleStore

log = logging.getLogger(__name__)

_COMPILE_PROMPT = """You compile a user's spoken/typed instruction about how Argus should behave
into a structured rule. Argus is a personal assistant that watches for events
(email arriving, focus changing, calendar reminders, etc.) and sometimes speaks
up about them; a "rule" changes that behavior going forward.

Instruction: {utterance}

If the scope is genuinely ambiguous (e.g. "stop telling me about that" -- about
WHAT, exactly?), reply with ONLY this JSON and nothing else:
{{"clarifying_question": "<one specific question that would resolve the ambiguity>"}}

Otherwise reply with ONLY this JSON and nothing else (no markdown fences, no
commentary):
{{
  "natural_language": "<the rule, read back in plain English, e.g. \\"Don't mention emails from newsletters@ anymore\\">",
  "kind": "suppression" | "preference" | "automation",
  "trigger": {{"kind": "<a dotted observation kind, e.g. mail.received>", "filters": [{{"field": "...", "op": "...", "value": "..."}}]}},
  "action": {{"type": "suppress" | "boost" | "notify" | "tool_call" | "activate_group" | "deactivate_group", ...}},
  "conditions": [],
  "until_condition": null,
  "group_name": null
}}

filters is an implicit AND; an empty list matches every observation of that
trigger kind. field is a dotted path against the observation: subject, source,
confidence, or payload.<key>. op is one of: eq, neq, contains, not_contains,
matches, gt, gte, lt, lte, in, fuzzy. Only use "fuzzy" when no literal
field/op combination captures the intent (e.g. "important-sounding" email).

If action.type is "tool_call" and the change should be undoable later, set
"reversible": true AND supply "read_tool"/"read_arguments" that read the
current state before the change -- omit reversible entirely if you can't."""


@dataclass
class CompiledRule:
    rule_id: int | None
    natural_language: str | None
    clarifying_question: str | None = None
    conflicts: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _strip_json_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"```\s*$", "", text)
    return text.strip()


class RuleCompiler:
    def __init__(self, router):
        self.router = router

    def compile(self, utterance: str, store: RuleStore) -> CompiledRule:
        raw = self._ask_llm(utterance)
        parsed = self._parse(raw)
        if parsed is None:
            return CompiledRule(
                rule_id=None, natural_language=None,
                clarifying_question="I couldn't turn that into a rule -- could you say it differently?",
            )

        if parsed.get("clarifying_question"):
            return CompiledRule(rule_id=None, natural_language=None, clarifying_question=parsed["clarifying_question"])

        natural_language = parsed.get("natural_language")
        if not natural_language or not parsed.get("trigger", {}).get("kind"):
            return CompiledRule(
                rule_id=None, natural_language=None,
                clarifying_question="I couldn't tell what event that rule should apply to -- could you be more specific?",
            )

        action, warnings = self._validate_action(parsed.get("action") or {})
        conflicts = self._detect_conflicts(parsed, action, store)

        rule_id = store.propose(
            natural_language=natural_language,
            source_utterance=utterance,
            kind=parsed.get("kind") or "preference",
            trigger=parsed.get("trigger") or {},
            action=action,
            conditions=parsed.get("conditions") or [],
            until_condition=parsed.get("until_condition"),
            group_name=parsed.get("group_name"),
            authorization=parsed.get("authorization"),
        )
        return CompiledRule(
            rule_id=rule_id, natural_language=natural_language,
            conflicts=conflicts, warnings=warnings,
        )

    def _ask_llm(self, utterance: str) -> str:
        prompt = _COMPILE_PROMPT.format(utterance=utterance)
        result = self.router.complete([Message(role="user", content=prompt)], force_tier=Tier.ADVANCED)
        return result.text

    def _parse(self, raw: str) -> dict | None:
        try:
            data = json.loads(_strip_json_fences(raw))
        except json.JSONDecodeError:
            log.warning("Rule compiler returned unparseable JSON")
            return None
        return data if isinstance(data, dict) else None

    def _validate_action(self, action: dict) -> tuple[dict, list[str]]:
        """The compiler must refuse to emit reversible: true without a
        working read_tool (Appendix A.3) -- downgrades and surfaces a
        warning rather than silently dropping the rule."""
        warnings: list[str] = []
        if action.get("type") == "tool_call" and action.get("reversible") and not action.get("read_tool"):
            action = {**action, "reversible": False}
            tool = action.get("tool", "the target")
            warnings.append(f"Marked non-reversible -- no read_tool was given to restore {tool} to its prior state.")
        return action, warnings

    def _detect_conflicts(self, parsed: dict, action: dict, store: RuleStore) -> list[dict]:
        """Surfaced now, at authoring time, not at runtime (§7.2 point 3).
        A same-trigger-kind, same-action-type overlap check -- not full
        semantic conflict detection, which would need its own LLM call
        this method deliberately doesn't make."""
        trigger_kind = (parsed.get("trigger") or {}).get("kind")
        action_type = action.get("type")
        if not trigger_kind or not action_type:
            return []
        conflicts = []
        for rule in store.list_active():
            if rule.trigger.get("kind") == trigger_kind and rule.action.get("type") == action_type:
                conflicts.append({"rule_id": rule.id, "natural_language": rule.natural_language})
        return conflicts
