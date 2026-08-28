from argus.llm.base import Message, Tier
from argus.tools.base import PermissionTier, Tool

# README dream/stretch item: "Second opinion" mode for big decisions --
# reason from a few angles (skeptic, domain expert, risk-focused)
# internally before giving one synthesized recommendation, rather than
# just answering from a single pass the way a normal reply does.
_ANGLES = [
    (
        "skeptic",
        "You're a sharp skeptic looking at someone else's decision. Don't "
        "be contrarian for its own sake, but push hard on the weakest "
        "assumption -- what's the thing everyone's taking for granted that "
        "might not hold?",
    ),
    (
        "domain expert",
        "You're an experienced practitioner in whatever domain this "
        "decision falls under. Set aside the framing given and think about "
        "what someone who's actually done this before would flag -- "
        "conventions, pitfalls, or context a newcomer would miss.",
    ),
    (
        "risk-focused",
        "You're evaluating this purely for downside risk. Ignore the "
        "upside for now -- what's the worst realistic outcome, how likely "
        "is it, and how reversible is it if it goes wrong?",
    ),
]

_SYNTHESIS_INSTRUCTION = (
    "You asked for a second opinion on a decision and got these independent "
    "internal takes, each reasoning from a different angle without seeing "
    "the others:\n\n{takes}\n\nThe decision: {question}\n\nSynthesize these "
    "into ONE clear recommendation, 3-5 sentences. Name the real tradeoff "
    "instead of just averaging the opinions together, and say what you'd "
    "actually do -- this needs to read as a single confident answer, not a "
    "committee summary."
)


def _build_second_opinion(router) -> Tool:
    def _second_opinion(args: dict) -> str:
        question = (args.get("question") or "").strip()
        if not question:
            return "error: no decision/question provided"

        takes = []
        for name, framing in _ANGLES:
            prompt = f"{framing}\n\nThe decision: {question}\n\nGive your honest take in 2-3 sentences."
            try:
                result = router.complete([Message(role="user", content=prompt)], force_tier=Tier.ADVANCED)
            except Exception as e:
                return f"error: failed getting the {name} perspective: {type(e).__name__}: {e}"
            takes.append((name, result.text.strip()))

        synthesis_prompt = _SYNTHESIS_INSTRUCTION.format(
            takes="\n\n".join(f"[{name}]: {text}" for name, text in takes),
            question=question,
        )
        try:
            synthesis = router.complete(
                [Message(role="user", content=synthesis_prompt)], force_tier=Tier.ADVANCED
            )
        except Exception as e:
            return f"error: failed synthesizing the perspectives: {type(e).__name__}: {e}"
        return synthesis.text.strip()

    return Tool(
        name="second_opinion",
        description=(
            "For a genuinely consequential decision the user is weighing (not a quick "
            "lookup) -- internally reasons from a skeptic's, a domain expert's, and a "
            "risk-focused angle, each independently, then synthesizes one clear "
            "recommendation. Slower and more expensive than a normal reply, so reserve it "
            "for when the user explicitly wants a second opinion or the stakes clearly "
            "warrant the extra depth, not routine questions."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The decision or question to get a second opinion on, with enough context to reason about.",
                }
            },
            "required": ["question"],
        },
        tier=PermissionTier.ALLOW,
        handler=_second_opinion,
    )
