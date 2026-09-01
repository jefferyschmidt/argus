import json
from unittest.mock import MagicMock

from argus.llm.base import CompletionResult, Tier
from argus.rules.compiler import RuleCompiler
from argus.rules.store import RuleStore


def _router(reply_text: str):
    router = MagicMock()
    router.complete.return_value = CompletionResult(text=reply_text, tier=Tier.ADVANCED, model="test")
    return router


def _store(tmp_path) -> RuleStore:
    return RuleStore(tmp_path / "argus.db")


def test_compile_stores_a_proposed_rule_and_reads_it_back(tmp_path):
    store = _store(tmp_path)
    reply = json.dumps({
        "natural_language": "Don't tell me about newsletters",
        "kind": "suppression",
        "trigger": {"kind": "mail.received", "filters": []},
        "action": {"type": "suppress"},
        "conditions": [], "until_condition": None, "group_name": None,
    })
    compiler = RuleCompiler(_router(reply))

    result = compiler.compile("stop telling me about newsletters", store)

    assert result.clarifying_question is None
    assert result.natural_language == "Don't tell me about newsletters"
    assert result.rule_id is not None
    rule = store.get(result.rule_id)
    assert rule.status == "proposed"
    assert rule.trigger == {"kind": "mail.received", "filters": []}


def test_compile_strips_markdown_fences(tmp_path):
    store = _store(tmp_path)
    reply = "```json\n" + json.dumps({
        "natural_language": "x", "kind": "preference",
        "trigger": {"kind": "mail.received", "filters": []}, "action": {"type": "boost", "amount": 0.2},
    }) + "\n```"
    compiler = RuleCompiler(_router(reply))

    result = compiler.compile("boost mail", store)
    assert result.rule_id is not None


def test_compile_surfaces_clarifying_question_without_storing_a_rule(tmp_path):
    store = _store(tmp_path)
    reply = json.dumps({"clarifying_question": "Stop telling you about which sender or topic?"})
    compiler = RuleCompiler(_router(reply))

    result = compiler.compile("stop telling me about that", store)

    assert result.rule_id is None
    assert result.clarifying_question == "Stop telling you about which sender or topic?"
    assert store.list_pending() == []


def test_compile_unparseable_response_asks_to_rephrase_without_raising(tmp_path):
    store = _store(tmp_path)
    compiler = RuleCompiler(_router("not json at all"))

    result = compiler.compile("do a thing", store)

    assert result.rule_id is None
    assert result.clarifying_question is not None
    assert store.list_pending() == []


def test_compile_missing_trigger_kind_asks_to_clarify(tmp_path):
    store = _store(tmp_path)
    reply = json.dumps({"natural_language": "x", "kind": "preference", "trigger": {}, "action": {"type": "boost"}})
    compiler = RuleCompiler(_router(reply))

    result = compiler.compile("do something vague", store)

    assert result.rule_id is None
    assert result.clarifying_question is not None


def test_reversible_tool_call_without_read_tool_is_downgraded_with_a_warning(tmp_path):
    store = _store(tmp_path)
    reply = json.dumps({
        "natural_language": "Turn the office light blue when a build fails",
        "kind": "automation",
        "trigger": {"kind": "task.failed", "filters": []},
        "action": {"type": "tool_call", "tool": "home_assistant_set_light", "arguments": {}, "reversible": True},
    })
    compiler = RuleCompiler(_router(reply))

    result = compiler.compile("blue light on build failure", store)

    rule = store.get(result.rule_id)
    assert rule.action["reversible"] is False
    assert result.warnings and "non-reversible" in result.warnings[0]


def test_reversible_tool_call_with_read_tool_is_preserved(tmp_path):
    store = _store(tmp_path)
    reply = json.dumps({
        "natural_language": "x", "kind": "automation",
        "trigger": {"kind": "task.failed", "filters": []},
        "action": {
            "type": "tool_call", "tool": "home_assistant_set_light", "arguments": {},
            "reversible": True, "read_tool": "home_assistant_get_state", "read_arguments": {},
        },
    })
    compiler = RuleCompiler(_router(reply))

    result = compiler.compile("blue light on build failure", store)

    rule = store.get(result.rule_id)
    assert rule.action["reversible"] is True
    assert result.warnings == []


def test_conflicting_rule_is_detected_against_active_rules(tmp_path):
    store = _store(tmp_path)
    existing_id = store.propose(
        natural_language="Suppress newsletters", kind="suppression",
        trigger={"kind": "mail.received", "filters": []}, action={"type": "suppress"},
    )
    store.confirm(existing_id)

    reply = json.dumps({
        "natural_language": "Suppress promo emails too", "kind": "suppression",
        "trigger": {"kind": "mail.received", "filters": []}, "action": {"type": "suppress"},
    })
    compiler = RuleCompiler(_router(reply))

    result = compiler.compile("suppress promo emails too", store)

    assert len(result.conflicts) == 1
    assert result.conflicts[0]["rule_id"] == existing_id


def test_no_conflict_when_action_types_differ(tmp_path):
    store = _store(tmp_path)
    existing_id = store.propose(
        natural_language="Boost Julia's mail", kind="preference",
        trigger={"kind": "mail.received", "filters": []}, action={"type": "boost", "amount": 0.3},
    )
    store.confirm(existing_id)

    reply = json.dumps({
        "natural_language": "Suppress newsletters", "kind": "suppression",
        "trigger": {"kind": "mail.received", "filters": []}, "action": {"type": "suppress"},
    })
    compiler = RuleCompiler(_router(reply))

    result = compiler.compile("suppress newsletters", store)
    assert result.conflicts == []


def test_authorization_is_captured_in_the_same_confirmation(tmp_path):
    store = _store(tmp_path)
    reply = json.dumps({
        "natural_language": "x", "kind": "automation",
        "trigger": {"kind": "task.failed", "filters": []},
        "action": {"type": "notify", "channel": "speak", "text": "build failed"},
        "authorization": {"scope": "notifications"},
    })
    compiler = RuleCompiler(_router(reply))

    result = compiler.compile("notify me on build failure", store)

    rule = store.get(result.rule_id)
    assert rule.authorization == {"scope": "notifications"}


def test_ask_llm_uses_advanced_tier(tmp_path):
    store = _store(tmp_path)
    router = _router(json.dumps({
        "natural_language": "x", "kind": "preference",
        "trigger": {"kind": "mail.received", "filters": []}, "action": {"type": "boost", "amount": 0.1},
    }))
    RuleCompiler(router).compile("boost mail", store)

    _, kwargs = router.complete.call_args
    assert kwargs["force_tier"] == Tier.ADVANCED
