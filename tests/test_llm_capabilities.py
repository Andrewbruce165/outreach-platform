"""Wave-0 RED scaffold — LLM capability gating + clamp/green-corridor (LLMP-09/10).

These target `app.services.llm.capabilities` (NOT yet built). Imports are DEFERRED
into each test body so `--collect-only` stays clean before the module exists (mirrors
the Phase 13/16/17 scaffold pattern). Behavioural assertions FAIL now (RED) and pass
once plan 18-02 lands the capability map + clamp helpers.

Decisions covered:
  D-09 — temperature / reasoning-effort / max-tokens knobs, capability-gated per model.
  D-10 — backend hard-clamp + green corridor; reasoning max-tokens floor ≥4000 (the
         2026-07-02 gpt-5-mini empty-response incident lesson).

Pitfall 2 (research): effort→budget mapping must return budget < max_tokens.
"""

import pytest


def test_reasoning_model_gate():
    """is_reasoning_model gates the reasoning families (OpenAI gpt-5*/o*), NOT gpt-4o*,
    and NOT Claude (Claude reasoning is gated separately via provider capabilities)."""
    from app.services.llm.capabilities import is_reasoning_model

    assert is_reasoning_model("gpt-5-mini") is True
    assert is_reasoning_model("gpt-5-mini-2025-08-07") is True
    assert is_reasoning_model("o3-mini") is True
    assert is_reasoning_model("gpt-4o-mini") is False
    assert is_reasoning_model("gpt-4o") is False
    # Claude reasoning is NOT gated by this OpenAI-family helper (adaptive/effort
    # capability comes from Anthropic /v1/models, not an ID prefix).
    assert is_reasoning_model("claude-sonnet-4-5") is False


def test_temperature_gated_off_for_openai_reasoning():
    """Temperature is NOT allowed for OpenAI reasoning models (400 unsupported_value),
    but IS allowed for gpt-4o* and every claude-* model."""
    from app.services.llm.capabilities import supports_temperature

    # OpenAI reasoning models reject temperature.
    assert supports_temperature("gpt-5-mini") is False
    assert supports_temperature("o3-mini") is False
    # Non-reasoning OpenAI models accept it (0.0–2.0).
    assert supports_temperature("gpt-4o-mini") is True
    assert supports_temperature("gpt-4o") is True
    # Anthropic accepts temperature (0.0–1.0).
    assert supports_temperature("claude-sonnet-4-5") is True
    assert supports_temperature("claude-opus-4-5") is True


def test_max_tokens_clamp_reasoning_floor():
    """max_tokens clamp enforces the D-10 reasoning floor (≥4000) and a sane ceiling.

    A client must not be able to set a tiny budget on a reasoning model and reproduce
    the 2026-07-02 ghosted-contact incident (whole budget eaten by hidden reasoning →
    content='' finish_reason='length')."""
    from app.services.llm.capabilities import clamp_max_tokens

    # Below-floor on a reasoning model is raised to at least 4000.
    assert clamp_max_tokens("gpt-5-mini", 500) >= 4000
    assert clamp_max_tokens("o3-mini", 100) >= 4000
    # Absurdly-large is clamped to a sane ceiling (not passed through unbounded).
    ceiled = clamp_max_tokens("gpt-5-mini", 200000)
    assert ceiled < 200000
    assert ceiled >= 4000


def test_effort_to_budget_below_max_tokens():
    """effort→thinking-budget mapping returns budget < max_tokens for every level
    (Pitfall 2: budget_tokens >= max_tokens → Anthropic 400), and 'minimal' → 0
    (omit thinking entirely)."""
    from app.services.llm.capabilities import effort_to_budget

    max_tokens = 4000
    for effort in ("minimal", "low", "medium", "high"):
        budget = effort_to_budget(effort, max_tokens)
        assert budget < max_tokens, f"{effort}: budget {budget} must be < max_tokens {max_tokens}"
        assert budget >= 0

    # 'minimal' disables thinking (budget 0 => omit the thinking block).
    assert effort_to_budget("minimal", max_tokens) == 0


def test_anthropic_uses_adaptive_thinking_gate():
    """Claude-5-generation models (+ Opus 4.7/4.8) require the adaptive thinking
    shape and 400 on the older manual budget_tokens shape (18-05 live UAT hit this
    against claude-sonnet-5); older Claude models keep the manual-budget path."""
    from app.services.llm.capabilities import anthropic_uses_adaptive_thinking

    assert anthropic_uses_adaptive_thinking("claude-sonnet-5") is True
    assert anthropic_uses_adaptive_thinking("claude-opus-4-8") is True
    assert anthropic_uses_adaptive_thinking("claude-sonnet-4-5") is False
    assert anthropic_uses_adaptive_thinking("claude-3-5-sonnet-latest") is False


def test_effort_to_anthropic_level_only_low_medium_high():
    """Anthropic's adaptive `effort` param accepts only low/medium/high — 'minimal'
    (OpenAI-only concept) and unknown values map to None (omit thinking entirely)."""
    from app.services.llm.capabilities import effort_to_anthropic_level

    assert effort_to_anthropic_level("low") == "low"
    assert effort_to_anthropic_level("medium") == "medium"
    assert effort_to_anthropic_level("high") == "high"
    assert effort_to_anthropic_level("minimal") is None
    assert effort_to_anthropic_level(None) is None
