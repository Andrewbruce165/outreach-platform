"""Wave-0 RED scaffold — server-side model-list family/capability filter (LLMP-08).

Targets `app.services.llm.capabilities.filter_chat_models` (NOT yet built). Deferred imports
keep --collect-only clean. Behavioural assertions FAIL now (RED) and pass once plan 18-03
lands the filter.

D-08: the live /v1/models list is server-side filtered to chat-with-tools-compatible models
only — no embeddings / whisper / tts / dall-e / realtime / deprecated. Family whitelist:
gpt-4o* / gpt-5* / o* for OpenAI; claude-* for Anthropic.
"""

import pytest


def test_openai_family_filter():
    """OpenAI raw id list → keeps chat/reasoning families, drops embeddings/whisper/
    dall-e/realtime."""
    from app.services.llm.capabilities import filter_chat_models

    raw = [
        "gpt-4o",
        "gpt-5-mini",
        "text-embedding-3-small",
        "whisper-1",
        "dall-e-3",
        "o3-mini",
        "gpt-4o-realtime-preview",
    ]
    kept = filter_chat_models("openai", raw)

    assert "gpt-4o" in kept
    assert "gpt-5-mini" in kept
    assert "o3-mini" in kept
    # Dropped: non-chat / non-tool-capable families.
    assert "text-embedding-3-small" not in kept
    assert "whisper-1" not in kept
    assert "dall-e-3" not in kept
    assert "gpt-4o-realtime-preview" not in kept


def test_anthropic_family_filter():
    """Anthropic raw id list → keeps claude-* ids, drops anything non-claude."""
    from app.services.llm.capabilities import filter_chat_models

    raw = [
        "claude-sonnet-4-5",
        "claude-opus-4-5",
        "claude-haiku-4-5",
        "text-embedding-3-small",  # not a claude model
        "gpt-4o",                   # wrong provider family
        "some-random-model",
    ]
    kept = filter_chat_models("anthropic", raw)

    assert "claude-sonnet-4-5" in kept
    assert "claude-opus-4-5" in kept
    assert "claude-haiku-4-5" in kept
    # Dropped: non-claude ids.
    assert "text-embedding-3-small" not in kept
    assert "gpt-4o" not in kept
    assert "some-random-model" not in kept
