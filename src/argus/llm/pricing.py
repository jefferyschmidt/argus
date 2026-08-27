from argus.llm.base import Tier

# USD per million tokens (input, output). Update if pricing changes.
_PRICE_PER_MTOK = {
    Tier.FAST: (1.0, 5.0),
    Tier.ADVANCED: (3.0, 15.0),
}


def estimate_cost(tier: Tier, input_tokens: int, output_tokens: int) -> float:
    if tier not in _PRICE_PER_MTOK:
        return 0.0
    in_price, out_price = _PRICE_PER_MTOK[tier]
    return (input_tokens / 1_000_000) * in_price + (output_tokens / 1_000_000) * out_price
