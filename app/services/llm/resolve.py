"""Phase 18 — workspace LLM-config resolution + provider factory + fallback classifier.

Resolution policy (D-02/D-03): absence of an llm_settings row (or a row without a
VALID key) = the platform default — the exact behaviour today (platform OPENAI_API_KEY
+ settings.openai_model), so nothing changes until a client explicitly configures a
valid BYO key. `is_key_level_error` (D-06) decides when a runtime error means the BYO
key is bad (→ fall back to the platform default + flag the key) vs a transient error
that must NOT swap providers (Pitfall 6 — swapping on a 429/5xx would leak client
traffic onto the platform bill and mask the real load problem).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

import anthropic
import openai
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.services.encryption import decrypt_api_key
from app.services.llm.anthropic_provider import AnthropicProvider
from app.services.llm.base import LLMProvider
from app.services.llm.openai_provider import OpenAIProvider


@dataclass
class LLMConfig:
    """Resolved per-workspace LLM configuration handed to get_provider.

    key_source: 'platform' (no BYO configured), 'byok' (valid BYO key in use),
    or 'fallback' (BYO key errored at runtime, degraded to platform — D-06).
    """

    provider: str
    model: str
    decrypted_key: Optional[str]
    key_source: str
    temperature: Optional[float] = None
    reasoning_effort: Optional[str] = None
    max_tokens: Optional[int] = None


def PLATFORM_DEFAULT(settings) -> LLMConfig:
    """The platform default config (D-02) — byte-identical to today's behaviour:
    OpenAI, settings.openai_model, platform OPENAI_API_KEY, all knobs None so the
    existing ai_engine constants (AI_REASONING_EFFORT etc.) apply unchanged."""
    return LLMConfig(
        provider="openai",
        model=settings.openai_model,
        decrypted_key=os.environ.get("OPENAI_API_KEY"),
        key_source="platform",
        temperature=None,
        reasoning_effort=None,
        max_tokens=None,
    )


def platform_fallback_config(settings) -> LLMConfig:
    """Same as PLATFORM_DEFAULT but key_source='fallback' — used by ai_engine when
    a BYO key raises a key-level error at runtime (D-06)."""
    cfg = PLATFORM_DEFAULT(settings)
    cfg.key_source = "fallback"
    return cfg


async def resolve_llm_config(session: AsyncSession, workspace_id) -> LLMConfig:
    """Resolve the effective LLM config for a workspace.

    D-02/D-03: an absent row, a non-'valid' api_key_status, or a NULL encrypted key
    all resolve to PLATFORM_DEFAULT (switching a provider REQUIRES a validated key).
    A valid BYO row -> decrypt the key and use the row's provider/model/knobs.
    The decrypted key is NEVER logged.
    """
    settings = get_settings()

    row = (
        await session.execute(
            text(
                """
                SELECT provider, model, api_key_encrypted, api_key_status,
                       temperature, reasoning_effort, max_tokens
                  FROM llm_settings
                 WHERE workspace_id = :wid
                """
            ),
            {"wid": str(workspace_id) if isinstance(workspace_id, UUID) else workspace_id},
        )
    ).first()

    if row is None:
        return PLATFORM_DEFAULT(settings)

    (provider, model, api_key_encrypted, api_key_status,
     temperature, reasoning_effort, max_tokens) = row

    # D-03: a provider switch needs a VALID key. Anything else -> platform default.
    if api_key_status != "valid" or not api_key_encrypted:
        return PLATFORM_DEFAULT(settings)

    return LLMConfig(
        provider=provider,
        model=model,
        decrypted_key=decrypt_api_key(api_key_encrypted),
        key_source="byok",
        temperature=temperature,
        reasoning_effort=reasoning_effort,
        max_tokens=max_tokens,
    )


def is_key_level_error(e) -> bool:
    """True ONLY for key-level errors (invalid key / permission / quota / billing) —
    401/403/insufficient_quota/402. Transient 429 rate-limits, 5xx server errors and
    connection errors return False (Pitfall 6 — never swap providers on those).
    Verbatim taxonomy from RESEARCH Code Examples."""
    if isinstance(e, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return True
    if isinstance(e, openai.RateLimitError) and getattr(e, "code", None) == "insufficient_quota":
        return True
    if isinstance(e, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return True
    if isinstance(e, anthropic.APIStatusError) and getattr(e, "status_code", None) == 402:
        return True
    return False


def get_provider(config: LLMConfig) -> LLMProvider:
    """Build the concrete provider adapter for a resolved config."""
    if config.provider == "anthropic":
        return AnthropicProvider(api_key=config.decrypted_key, model=config.model)
    return OpenAIProvider(
        api_key=config.decrypted_key or os.environ.get("OPENAI_API_KEY"),
        model=config.model,
    )


async def probe_key(provider: str, api_key: str) -> bool:
    """D-05 test-connection probe — cheapest possible auth check for a BYO key.

    Builds a provider SDK client and calls `models.list()` (a GET that only needs the
    key to be valid — no completion tokens spent). Returns True on success; a bad key
    raises the SDK's AuthenticationError/PermissionDeniedError which the caller catches
    to report {status:'invalid'}. The api key is NEVER logged here (this module has zero
    logging on purpose — the plaintext BYO key can never reach app logs)."""
    if provider == "anthropic":
        client = anthropic.AsyncAnthropic(api_key=api_key)
    else:
        client = openai.AsyncOpenAI(api_key=api_key)
    await client.models.list()
    return True


async def list_model_ids(provider: str, api_key: str) -> list:
    """List the raw model ids for a provider via the SDK `models.list()` (D-08).

    Returns the UNFILTERED id strings; the caller runs `filter_models` to keep only
    chat-with-tools families. Any SDK error propagates so the caller can soft-fail
    (empty list + a note) instead of 500-crashing the settings page. Key never logged."""
    if provider == "anthropic":
        client = anthropic.AsyncAnthropic(api_key=api_key)
    else:
        client = openai.AsyncOpenAI(api_key=api_key)
    page = await client.models.list()
    ids: list = []
    for m in getattr(page, "data", []) or []:
        mid = getattr(m, "id", None)
        if mid:
            ids.append(mid)
    return ids
