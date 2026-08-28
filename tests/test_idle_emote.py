from unittest.mock import MagicMock

from argus.idle_emote import (
    _FALLBACK_SPECS,
    _clean_json_text,
    _validate_spec,
    generate_idle_emote,
)
from argus.llm.base import CompletionResult, Tier


def _router(reply_text):
    router = MagicMock()
    router.complete.return_value = CompletionResult(text=reply_text, tier=Tier.LOCAL, model="test")
    return router


def test_clean_json_text_strips_markdown_fences():
    assert _clean_json_text('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert _clean_json_text('{"a": 1}') == '{"a": 1}'


def test_validate_spec_accepts_well_formed_spec():
    spec = {"name": "party hat", "parts": [{"type": "arc", "cx": 0, "cy": -0.9, "r": 0.2, "a0": 0, "a1": 3, "share": 0.3}]}
    result = _validate_spec(spec)
    assert result["name"] == "party hat"
    assert len(result["parts"]) == 1


def test_validate_spec_rejects_missing_name():
    assert _validate_spec({"parts": [{"type": "ring", "share": 0.2}]}) is None


def test_validate_spec_rejects_empty_parts():
    assert _validate_spec({"name": "x", "parts": []}) is None


def test_validate_spec_drops_parts_with_unknown_type():
    spec = {"name": "x", "parts": [{"type": "hexagon", "share": 0.2}, {"type": "ring", "r": 0.1, "share": 0.2}]}
    result = _validate_spec(spec)
    assert len(result["parts"]) == 1
    assert result["parts"][0]["type"] == "ring"


def test_validate_spec_drops_parts_with_non_numeric_fields():
    spec = {"name": "x", "parts": [{"type": "ring", "r": "big", "share": 0.2}]}
    assert _validate_spec(spec) is None  # only bad part, nothing left


def test_validate_spec_clamps_wild_numeric_values():
    spec = {"name": "x", "parts": [{"type": "ring", "r": 999, "cx": -999, "share": 0.2}]}
    result = _validate_spec(spec)
    assert result["parts"][0]["r"] == 2.0
    assert result["parts"][0]["cx"] == -2.0


def test_validate_spec_caps_total_share_and_part_count():
    parts = [{"type": "blob", "r": 0.1, "share": 0.2} for _ in range(10)]
    spec = {"name": "x", "parts": parts}
    result = _validate_spec(spec)
    assert len(result["parts"]) <= 6
    assert sum(p["share"] for p in result["parts"]) <= 0.50 + 1e-9


def test_validate_spec_rejects_non_dict():
    assert _validate_spec("not a dict") is None
    assert _validate_spec(None) is None


def test_generate_idle_emote_uses_real_response_when_valid():
    router = _router('{"name": "tiny umbrella", "parts": [{"type": "line", "x1": 0, "y1": 0, "x2": 0, "y2": -0.3, "share": 0.2}]}')

    result = generate_idle_emote(router)

    assert result["name"] == "tiny umbrella"
    router.complete.assert_called_once()
    assert router.complete.call_args.kwargs["force_tier"] == Tier.LOCAL


def test_generate_idle_emote_falls_back_on_malformed_json():
    router = _router("not json at all")

    result = generate_idle_emote(router)

    assert result in _FALLBACK_SPECS


def test_generate_idle_emote_falls_back_on_invalid_spec():
    router = _router('{"name": "", "parts": []}')

    result = generate_idle_emote(router)

    assert result in _FALLBACK_SPECS


def test_generate_idle_emote_falls_back_on_router_exception():
    router = MagicMock()
    router.complete.side_effect = RuntimeError("boom")

    result = generate_idle_emote(router)

    assert result in _FALLBACK_SPECS


def test_generate_idle_emote_strips_markdown_fences_before_parsing():
    router = _router('```json\n{"name": "cat ears", "parts": [{"type": "ring", "r": 0.1, "share": 0.2}]}\n```')

    result = generate_idle_emote(router)

    assert result["name"] == "cat ears"
