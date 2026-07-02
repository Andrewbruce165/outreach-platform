"""Phase 18 — PURE capability gating + clamp + effort→budget helpers (D-09/D-10).

No I/O, no SDK imports — importable in isolation and cheaply unit-testable. These
encode the green-corridor policy the backend enforces regardless of what the UI sends:

  D-09  temperature / reasoning-effort / max-tokens knobs are capability-gated per model.
  D-10  backend HARD-clamps max_tokens: reasoning models floor at 4000 (the 2026-07-02
        gpt-5-mini ghosted-contact incident — a tiny budget was eaten by hidden reasoning
        tokens → content='' finish_reason='length', silently dropped), ceiling for all.

`is_reasoning_model` is the single source of truth for the OpenAI reasoning family
(gpt-5*/o1/o3/o4); plan 18-04 re-exports ai_engine's `_is_reasoning_model` from here so
the gate is defined once.
"""

from typing import Optional

REASONING_MAX_TOKENS_FLOOR = 4000  # D-10 — reasoning floor (2026-07-02 incident lesson)
NON_REASONING_DEFAULT_MAX_TOKENS = 4000  # when caller passes None on a non-reasoning model
MAX_TOKENS_CEILING = 32000  # green-corridor ceiling (all models)

# Claude manual thinking-budget per reasoning-effort level (research recommended table).
# 'minimal' => 0 => omit the thinking block entirely.
EFFORT_TO_BUDGET = {"minimal": 0, "low": 2000, "medium": 8000, "high": 16000}

# Headroom kept between the thinking budget and max_tokens so budget < max_tokens
# always holds (Anthropic 400s when budget_tokens >= max_tokens — Pitfall 2).
_THINKING_HEADROOM = 512


def is_reasoning_model(model: str) -> bool:
    """True for OpenAI reasoning models (gpt-5*, o1/o3/o4*) that split
    max_completion_tokens between hidden reasoning and visible output and accept
    `reasoning_effort`. Claude reasoning is NOT gated here — its adaptive/effort
    capability comes from the Anthropic /v1/models metadata, not an ID prefix."""
    m = (model or "").lower()
    return m.startswith(("gpt-5", "o1", "o3", "o4"))


def supports_temperature(model: str) -> bool:
    """Temperature support gate. OpenAI reasoning models reject temperature with a
    400 unsupported_value; non-reasoning OpenAI models (gpt-4o*) and ALL Claude
    models accept it. Claude is never an OpenAI reasoning model per is_reasoning_model,
    so `not is_reasoning_model` is correct for both providers."""
    return not is_reasoning_model(model)


def clamp_max_tokens(model: str, value: Optional[int]) -> int:
    """D-10 hard clamp. Reasoning models floor at REASONING_MAX_TOKENS_FLOOR (4000)
    so a client can't reproduce the ghosted-contact incident with a tiny budget;
    a ceiling caps every model so absurd values never pass through unbounded."""
    if value is None:
        value = (
            REASONING_MAX_TOKENS_FLOOR
            if is_reasoning_model(model)
            else NON_REASONING_DEFAULT_MAX_TOKENS
        )
    if is_reasoning_model(model):
        value = max(value, REASONING_MAX_TOKENS_FLOOR)
    return min(value, MAX_TOKENS_CEILING)


def effort_to_budget(effort: Optional[str], max_tokens: int) -> int:
    """Map a reasoning-effort level to a Claude manual thinking budget, guaranteed
    < max_tokens (Pitfall 2). 'minimal' (or unknown) -> 0 => omit thinking entirely."""
    budget = EFFORT_TO_BUDGET.get((effort or "").lower(), 0)
    if budget <= 0:
        return 0
    return min(budget, max(0, max_tokens - _THINKING_HEADROOM))


def filter_chat_models(provider: str, model_ids: list) -> list:
    """D-08 server-side model-list filter — keep only chat-with-tools families.

    The single filter implementation lives in `models_filter.filter_models`; this is
    the (provider, model_ids) alias the RED tests + the settings router import. Imported
    lazily to keep this pure module free of any circular-import risk."""
    from app.services.llm.models_filter import filter_models

    return filter_models(list(model_ids), provider=provider)
