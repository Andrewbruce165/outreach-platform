"""Unit tests for app.services.llm_pricing.compute_spend_cents.

Pure sync — no DB, no async. Covers:
- known-model pricing (input + output rate, USD per 1M tokens → integer cents)
- multi-row summation
- unknown model → 0 cents + logged warning (never raises)
- empty input → 0
- None token counts treated as 0
- versioned/suffixed model strings resolved by longest-prefix match
"""

import logging

from app.services.llm_pricing import (
    DEFAULT_PRICE,
    PRICING,
    compute_spend_cents,
)


def test_known_model_prices_input_and_output():
    # gpt-5-mini: input 0.25 USD/1M, output 2.00 USD/1M.
    # 1M prompt + 1M completion = 0.25 + 2.00 = 2.25 USD = 225 cents.
    cents = compute_spend_cents([("gpt-5-mini", 1_000_000, 1_000_000)])
    assert cents == 225


def test_gpt4o_mini_pricing():
    # input 0.15, output 0.60 → 1M+1M = 0.75 USD = 75 cents.
    cents = compute_spend_cents([("gpt-4o-mini", 1_000_000, 1_000_000)])
    assert cents == 75


def test_multiple_rows_sum():
    row_a = ("gpt-5-mini", 1_000_000, 1_000_000)      # 225 cents
    row_b = ("gpt-4o-mini", 1_000_000, 1_000_000)     # 75 cents
    total = compute_spend_cents([row_a, row_b])
    single = compute_spend_cents([row_a]) + compute_spend_cents([row_b])
    assert total == 225 + 75
    assert total == single


def test_versioned_model_prefix_match():
    # Real prod string: 'gpt-5-mini-2025-08-07' must price as gpt-5-mini.
    versioned = compute_spend_cents([("gpt-5-mini-2025-08-07", 1_000_000, 1_000_000)])
    base = compute_spend_cents([("gpt-5-mini", 1_000_000, 1_000_000)])
    assert versioned == base == 225


def test_claude_sonnet_prefix_wins_over_shorter_key():
    # claude-3-5-sonnet-20241022 must resolve to claude-3-5-sonnet
    # (longest-prefix), not a hypothetical shorter 'claude-3' key.
    cents = compute_spend_cents([("claude-3-5-sonnet-20241022", 1_000_000, 1_000_000)])
    # input 3.00 + output 15.00 = 18.00 USD = 1800 cents
    assert cents == 1800


def test_unknown_model_zero_and_warns(caplog):
    with caplog.at_level(logging.WARNING, logger="app.services.llm_pricing"):
        cents = compute_spend_cents([("totally-made-up-model", 1_000_000, 1_000_000)])
    assert cents == 0
    assert any("unknown model" in r.message.lower() for r in caplog.records)


def test_unknown_model_never_raises():
    # mix known + unknown — known still priced, unknown contributes 0.
    cents = compute_spend_cents(
        [("gpt-5-mini", 1_000_000, 1_000_000), ("nope-model", 5_000_000, 5_000_000)]
    )
    assert cents == 225


def test_empty_input_zero():
    assert compute_spend_cents([]) == 0


def test_none_token_counts_treated_as_zero():
    # prompt_tokens is nullable in llm_calls — None must not crash.
    assert compute_spend_cents([("gpt-5-mini", None, None)]) == 0
    # None prompt, real completion → prices only completion.
    only_out = compute_spend_cents([("gpt-5-mini", None, 1_000_000)])
    assert only_out == 200  # 2.00 USD


def test_rounding_no_per_row_drift():
    # Many tiny rows should be summed as floats and rounded ONCE, not per-row.
    # 3 rows of 100 completion tokens each at gpt-5-mini output 2.00/1M:
    # each = 100/1e6 * 2.00 * 100 = 0.02 cents; sum = 0.06 → rounds to 0.
    rows = [("gpt-5-mini", 0, 100)] * 3
    assert compute_spend_cents(rows) == 0
    # 100k completion tokens at 2.00/1M = 0.1 * 2.00 * 100 = 20 cents.
    assert compute_spend_cents([("gpt-5-mini", 0, 100_000)]) == 20


def test_default_price_is_zero():
    assert DEFAULT_PRICE == {"input": 0.0, "output": 0.0}


def test_pricing_table_has_expected_models():
    for key in ("gpt-5-mini", "gpt-4o-mini", "gpt-4o", "claude-3-5-sonnet",
                "claude-3-5-haiku"):
        assert key in PRICING
        assert "input" in PRICING[key] and "output" in PRICING[key]
