"""Per-model LLM pricing table + spend computation (USD → integer cents).

Prices are USD per 1M tokens (input/output separately). ``llm_calls.model``
strings are often versioned/suffixed (e.g. ``gpt-5-mini-2025-08-07``,
``claude-3-5-sonnet-20241022``), so pricing resolves by LONGEST-PREFIX match:
we sort keys by length descending so a more specific key
(``claude-3-5-sonnet``) wins over a shorter one (``claude-3``).

Unknown / unpriceable models fall back to ``DEFAULT_PRICE`` (zero) and emit a
single WARNING — spend is counted as 0, never raises, so analytics endpoints
degrade gracefully instead of crashing on a new model string.

No DB, no schema — pricing lives entirely in code. Adjust ``PRICING`` when a
new model appears in ``SELECT DISTINCT model FROM llm_calls``.
"""

import logging

logger = logging.getLogger(__name__)


# USD per 1M tokens. Values as of 2026-07.
# Matched by prefix against llm_calls.model (longest key wins).
PRICING: dict[str, dict[str, float]] = {
    # OpenAI
    "gpt-5-mini": {"input": 0.25, "output": 2.00},   # prod: gpt-5-mini-2025-08-07
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},  # legacy rows may exist
    "gpt-4o": {"input": 2.50, "output": 10.00},
    # Anthropic
    "claude-sonnet-5": {"input": 3.00, "output": 15.00},  # prod: claude-sonnet-5
    "claude-3-5-sonnet": {"input": 3.00, "output": 15.00},
    "claude-3-5-haiku": {"input": 0.80, "output": 4.00},
}

DEFAULT_PRICE: dict[str, float] = {"input": 0.0, "output": 0.0}

# Precompute keys ordered by length desc so the longest (most specific) prefix
# wins during resolution.
_KEYS_BY_LEN = sorted(PRICING.keys(), key=len, reverse=True)


def _resolve_price(model: str) -> dict[str, float] | None:
    """Return the price dict for ``model`` by longest-prefix match, else None."""
    if not model:
        return None
    for key in _KEYS_BY_LEN:
        if model.startswith(key):
            return PRICING[key]
    return None


def compute_spend_cents(
    rows: list[tuple[str, int | None, int | None]],
) -> int:
    """Sum USD spend (in integer cents) over ``(model, prompt_tokens, completion_tokens)`` rows.

    - Price resolved by longest-prefix match against ``PRICING``.
    - Unknown model → 0 cents for that row + a WARNING (never raises).
    - None token counts treated as 0 (``llm_calls.prompt_tokens`` is nullable).
    - Floats are accumulated and rounded ONCE at the end to avoid per-row
      rounding drift; the total is returned as int cents.
    """
    total_cents = 0.0
    for model, prompt_tokens, completion_tokens in rows:
        price = _resolve_price(model)
        if price is None:
            logger.warning(
                "llm_pricing: unknown model %s — spend counted as 0", model
            )
            price = DEFAULT_PRICE
        p = prompt_tokens or 0
        c = completion_tokens or 0
        total_cents += (p / 1e6) * price["input"] * 100
        total_cents += (c / 1e6) * price["output"] * 100
    return round(total_cents)
