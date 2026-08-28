"""On-the-fly idle emotes: instead of picking from a hand-authored library,
ask the LLM to invent a small scene for the particle-swarm face to form
while genuinely idle -- "reading glasses and a book," "a coffee mug,"
whatever it comes up with, truly one-off rather than replayed from a fixed
set. See argus/ui/static/index.html's idle-emote system for how a returned
spec actually gets rendered (a generic interpreter over a small primitive
vocabulary: ring/rect/arc/line/blob), and its scheduler for how often this
gets called.

The model only ever designs the ACCESSORY -- the base head silhouette is
always rendered client-side regardless of what comes back here. That's a
deliberate safety/quality bound, not a limitation worth lifting: it caps
the blast radius of a bad generation (worst case, an odd-looking prop next
to a normal head, never an unrecognizable blob) and it's true to what was
actually asked for -- "the swarm forms into HIM with glasses on," an
accessory on a recognizable Argus, not a replacement for him."""

import json
import logging
import random
import re

from argus.llm.base import Message, Tier

log = logging.getLogger(__name__)

_ALLOWED_PART_TYPES = {"ring", "rect", "arc", "line", "blob"}
_MAX_PARTS = 6
_MAX_TOTAL_SHARE = 0.50  # leaves >= 0.50 for the always-present head

_PROMPT = """Design a small, funny one-off "accessory" scene for a particle-swarm \
face to briefly form while it's sitting idle -- like a screensaver personality \
beat. Think: what would be a charming, silly thing to see a digital assistant's \
avatar briefly do or wear when nobody's talking to it?

Coordinate system: everything is a fraction of the face's own scale, relative \
to the face center at (0, 0). Negative y is up, positive y is down. The face \
itself already occupies roughly x in [-0.6, 0.6], y in [-0.8, 0.8] -- design \
your accessory to sit ON or NEAR that silhouette (above it, beside it, in \
front of it, held next to it -- your call), not covering the whole canvas.

Available part shapes (use ONLY these "type" values):
- "ring": a circle outline. Fields: cx, cy, r (all fractions of scale).
- "rect": a rectangle outline. Fields: cx, cy, w, h.
- "arc": a partial circle (e.g. a handle, a smile, a curl). Fields: cx, cy, r, a0, a1 (angles in radians, 0 = right, increasing clockwise).
- "line": a straight segment. Fields: x1, y1, x2, y2.
- "blob": a soft round cluster of particles (e.g. a puff, a cheek, a splat). Fields: cx, cy, r.

Reply with ONLY JSON, no markdown fences, no commentary, matching exactly:
{"name": "short funny name (2-4 words)", "parts": [{"type": "...", "share": 0.0-0.5, ...fields...}, ...]}

2-5 parts. Each part's "share" is roughly how many of the accessory's \
particles it gets (they should sum to somewhere around 0.3-0.5 total -- \
the rest of the swarm is always the face itself, not yours to use).

Pick ONE idea from a genuinely different category each time, not the most \
obvious default -- vary where it sits, not just what it is:
- worn on the head (NOT always a hat -- antennae, a halo, a bandana, a crown)
- held or floating beside the face (an umbrella, a balloon, a magic wand, a fishing rod)
- on the face itself (glasses, a monocle, a mustache, a single raised eyebrow made literal)
- around the body/neck (a bowtie, a scarf, a cape corner, a name tag)
- an emitted/ambient effect (musical notes, sparkles, a thought-bubble question mark, sweat drops)
Be genuinely surprising -- if your first instinct is a hat or glasses, pick \
something else instead."""

# Fallbacks if generation fails or the response can't be trusted -- the
# idle experience should never break because of this, and these use the
# exact same schema/interpreter as a real generation, so there's no
# separate code path to keep in sync.
_FALLBACK_SPECS = [
    {
        "name": "reading glasses",
        "parts": [
            {"type": "ring", "cx": -0.16, "cy": -0.30, "r": 0.11, "share": 0.18},
            {"type": "ring", "cx": 0.16, "cy": -0.30, "r": 0.11, "share": 0.18},
            {"type": "line", "x1": -0.05, "y1": -0.30, "x2": 0.05, "y2": -0.30, "share": 0.04},
        ],
    },
    {
        "name": "party hat",
        "parts": [
            {"type": "arc", "cx": 0.0, "cy": -0.95, "r": 0.28, "a0": 3.4, "a1": 6.0, "share": 0.30},
            {"type": "blob", "cx": 0.0, "cy": -1.18, "r": 0.05, "share": 0.10},
        ],
    },
    {
        "name": "coffee mug",
        "parts": [
            {"type": "rect", "cx": 0.42, "cy": 0.30, "w": 0.34, "h": 0.40, "share": 0.28},
            {"type": "arc", "cx": 0.66, "cy": 0.30, "r": 0.14, "a0": -1.6, "a1": 1.6, "share": 0.10},
            {"type": "blob", "cx": 0.42, "cy": 0.05, "r": 0.08, "share": 0.08},
        ],
    },
]


def _clean_json_text(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"```\s*$", "", text)
    return text.strip()


def _validate_spec(spec: dict) -> dict | None:
    """Defensive, manual validation -- no schema library needed for
    something this small, and this is the one place a bad/hallucinated LLM
    response could otherwise reach the renderer with garbage numeric
    fields. Returns a cleaned spec (parts trimmed/clamped) or None if the
    response isn't trustworthy enough to use at all."""
    if not isinstance(spec, dict):
        return None
    name = spec.get("name")
    parts = spec.get("parts")
    if not isinstance(name, str) or not name.strip() or not isinstance(parts, list) or not parts:
        return None

    cleaned_parts = []
    total_share = 0.0
    for part in parts[:_MAX_PARTS]:
        if not isinstance(part, dict) or part.get("type") not in _ALLOWED_PART_TYPES:
            continue
        share = part.get("share")
        if not isinstance(share, (int, float)) or share <= 0:
            continue
        share = min(float(share), 0.35)
        remaining_budget = _MAX_TOTAL_SHARE - total_share
        if remaining_budget <= 0:
            break
        share = min(share, remaining_budget)
        numeric_fields = {k: v for k, v in part.items() if k not in ("type", "share")}
        if not all(isinstance(v, (int, float)) for v in numeric_fields.values()):
            continue
        # Clamp every numeric field to a sane range -- keeps a wild value
        # (e.g. r: 40) from producing a part that swallows the whole canvas.
        clamped = {k: max(-2.0, min(2.0, float(v))) for k, v in numeric_fields.items()}
        clamped["type"] = part["type"]
        clamped["share"] = share
        cleaned_parts.append(clamped)
        total_share += share

    if not cleaned_parts:
        return None
    return {"name": name.strip()[:60], "parts": cleaned_parts}


def generate_idle_emote(router) -> dict:
    """Never raises -- falls back to a random built-in spec on any failure
    (bad JSON, a validation miss, the call itself failing), so a flaky
    generation can never take down the idle animation."""
    try:
        result = router.complete([Message(role="user", content=_PROMPT)], force_tier=Tier.LOCAL)
        raw = _clean_json_text(result.text)
        spec = json.loads(raw)
        validated = _validate_spec(spec)
        if validated:
            return validated
        log.info("Idle emote generation returned an unusable spec; using a fallback")
    except Exception:
        log.exception("Idle emote generation failed; using a fallback")
    return random.choice(_FALLBACK_SPECS)
