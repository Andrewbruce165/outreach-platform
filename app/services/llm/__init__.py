# Phase 18 provider-adapter package.
#
# A single choke point so the answerer + warmup route through ANY LLM provider
# without per-call `if provider` branching. Public surface (wired progressively
# across plans 18-02..18-04):
#   - LLMResult / ToolCall / LLMProvider          (base.py)
#   - capability helpers (clamp/temperature/effort) (capabilities.py)
#   - OpenAIProvider / AnthropicProvider           (openai_provider.py / anthropic_provider.py)
#   - resolve_llm_config / get_provider / is_key_level_error / LLMConfig (resolve.py)
#
# resolve.py re-exports (18-02 Task 3) — the public surface downstream plans import.
from app.services.llm.base import LLMProvider, LLMResult, ToolCall
from app.services.llm.resolve import (
    LLMConfig,
    get_provider,
    is_key_level_error,
    platform_fallback_config,
    resolve_llm_config,
)

__all__ = [
    "LLMProvider",
    "LLMResult",
    "ToolCall",
    "LLMConfig",
    "get_provider",
    "is_key_level_error",
    "platform_fallback_config",
    "resolve_llm_config",
]
