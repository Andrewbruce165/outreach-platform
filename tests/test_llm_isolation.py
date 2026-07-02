"""Wave-0 scaffold — Whisper + KB embeddings pinned to the platform OpenAI singleton (LLMP-12).

D-12: Whisper voice transcription AND KB embeddings (ingest + search) ALWAYS stay on the
platform OpenAI key (the module-level AsyncOpenAI singleton in ai_engine.py:41) regardless
of the workspace's provider choice — Anthropic has no such APIs, so choosing Claude must not
break voice or KB.

These are introspection / grep guards (no network, no DB). Unlike the other Wave-0 files
they are a PRESERVATION guard: they pass NOW and MUST KEEP passing after plan 18-04 wires
the answerer/warmup onto the new adapter. If a later plan accidentally routes Whisper or
embeddings through the per-workspace `app.services.llm` factory, these turn RED — which is
exactly the D-12 regression we want to catch.
"""

import inspect

import pytest


def test_whisper_uses_platform_singleton():
    """ai_engine.transcribe_audio must call the module-level platform `client`
    (AsyncOpenAI) and must NOT build a per-workspace client or call the provider factory."""
    from app.services import ai_engine

    src = inspect.getsource(ai_engine.AIEngine.transcribe_audio)

    # Uses the platform singleton for Whisper.
    assert "client.audio.transcriptions" in src, (
        "transcribe_audio must use the module-level platform client for Whisper (D-12)"
    )
    # Does NOT build a per-workspace client or reach the new provider factory.
    assert "AsyncAnthropic" not in src, "Whisper must not touch Anthropic (D-12)"
    assert "get_provider" not in src, "Whisper must not go through the LLM provider factory (D-12)"
    assert "build_client" not in src, "Whisper must not build a per-workspace client (D-12)"


def test_kb_embeddings_use_platform_singleton():
    """KB ingest + search embeddings must reference the platform singleton
    (`ai_engine.client`), not the new per-workspace `app.services.llm` factory."""
    from app.services import kb_ingest, kb_search

    ingest_src = inspect.getsource(kb_ingest)
    search_src = inspect.getsource(kb_search)

    # Ingest reuses the platform AsyncOpenAI client for embeddings (D-12).
    assert "from app.services.ai_engine import client" in ingest_src or \
        "ai_engine.client" in ingest_src, (
        "kb_ingest must embed via the platform ai_engine.client singleton (D-12)"
    )
    assert "client.embeddings" in ingest_src, "kb_ingest must call client.embeddings (D-12)"

    # Neither KB module may route embeddings through the new provider factory.
    for name, src in (("kb_ingest", ingest_src), ("kb_search", search_src)):
        assert "get_provider" not in src, f"{name} must not use the LLM provider factory for embeddings (D-12)"
        assert "AsyncAnthropic" not in src, f"{name} must not touch Anthropic (D-12)"
