# NOTE (Phase 18): this file will be updated in plan 18-04 to patch the LLM adapter path
# (provider.complete) instead of ai_engine.client.chat.completions.create directly, so the
# empty-guard/retry works through the adapter for BOTH providers. Left unchanged here to keep
# the Wave-0 scaffold collect-clean; do not rewrite it in 18-01.
"""Regression: reasoning-model empty-response handling (fix: ai-empty-llm-response).

Incident 2026-07-02: OPENAI_MODEL was switched to a reasoning model
(gpt-5-mini). Reasoning models count hidden reasoning tokens against
max_completion_tokens, so a hard turn could spend the whole (2000-token)
budget on reasoning and return content='' with finish_reason='length' and NO
error. generate_response returned None → the listener sent nothing → contacts
were silently ghosted (real users Polina/Mariya).

These tests lock in the fix:
  1. _is_reasoning_model gates the reasoning_effort param (so OPENAI_MODEL can
     be rolled back to gpt-4o-mini without a 400).
  2. _build_completion_params adds reasoning_effort + a generous token budget
     for reasoning models, escalates both on retry, and omits reasoning_effort
     for non-reasoning models.
  3. generate_response retries ONCE (minimal reasoning + bigger budget) when the
     first completion is empty with finish_reason='length', and returns the
     retry's text instead of silently dropping the reply.
"""

import uuid as _uuid

import pytest
from unittest.mock import AsyncMock, patch

from app.services import ai_engine
from app.services.ai_engine import (
    AI_MAX_COMPLETION_TOKENS,
    AI_MAX_COMPLETION_TOKENS_RETRY,
    AI_REASONING_EFFORT,
    AI_REASONING_EFFORT_RETRY,
    _build_completion_params,
    _is_reasoning_model,
)
from tests.utils.openai_mocks import make_openai_response

pytestmark = pytest.mark.asyncio


# ─── _is_reasoning_model ────────────────────────────────────────────────────

async def test_is_reasoning_model_matches_reasoning_families():
    for m in ("gpt-5-mini-2025-08-07", "gpt-5", "o1-mini", "o3", "o4-mini"):
        assert _is_reasoning_model(m) is True, m


async def test_is_reasoning_model_false_for_chat_models():
    # gpt-4o* etc. reject reasoning_effort with a 400 — must NOT be flagged.
    for m in ("gpt-4o-mini", "gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo", "", None):
        assert _is_reasoning_model(m) is False, m


# ─── _build_completion_params ───────────────────────────────────────────────

async def test_build_params_reasoning_model_adds_effort_and_budget(monkeypatch):
    monkeypatch.setattr(ai_engine.settings, "openai_model", "gpt-5-mini-2025-08-07")
    p = _build_completion_params([{"role": "user", "content": "hi"}])
    assert p["reasoning_effort"] == AI_REASONING_EFFORT
    assert p["max_completion_tokens"] == AI_MAX_COMPLETION_TOKENS
    assert "tools" not in p  # no tools passed


async def test_build_params_retry_escalates(monkeypatch):
    monkeypatch.setattr(ai_engine.settings, "openai_model", "gpt-5-mini-2025-08-07")
    p = _build_completion_params([{"role": "user", "content": "hi"}], retry=True)
    assert p["reasoning_effort"] == AI_REASONING_EFFORT_RETRY
    assert p["max_completion_tokens"] == AI_MAX_COMPLETION_TOKENS_RETRY
    assert AI_MAX_COMPLETION_TOKENS_RETRY > AI_MAX_COMPLETION_TOKENS


async def test_build_params_non_reasoning_omits_effort(monkeypatch):
    monkeypatch.setattr(ai_engine.settings, "openai_model", "gpt-4o-mini")
    p = _build_completion_params(
        [{"role": "user", "content": "hi"}], tools=[{"type": "function"}]
    )
    assert "reasoning_effort" not in p  # would 400 on gpt-4o-mini
    assert p["tools"] == [{"type": "function"}]
    assert p["tool_choice"] == "auto"


# ─── generate_response: empty-length → retry → non-empty ────────────────────

async def test_empty_length_response_retries_and_returns_text(
    monkeypatch, async_db_session, test_workspace, test_agent_factory,
):
    """First completion empty + finish_reason='length' → one retry with minimal
    reasoning + bigger budget → returns the retry's text (never a silent None)."""
    monkeypatch.setattr(ai_engine.settings, "openai_model", "gpt-5-mini-2025-08-07")
    agent = await test_agent_factory(name="Empty Retry Agent")

    empty = make_openai_response(text_content="", finish_reason="length")
    recovered = make_openai_response(
        text_content="Привет! Расскажу подробнее про завтрак 18 июля.",
        finish_reason="stop",
    )
    mock_create = AsyncMock(side_effect=[empty, recovered])

    with patch.object(ai_engine.client.chat.completions, "create", new=mock_create):
        reply = await ai_engine.ai_engine.generate_response(
            session=async_db_session,
            conversation_id=str(_uuid.uuid4()),
            context_id=str(agent.id),
            contact_name="Polina",
            new_message="Нет расскажи",
        )

    # Retry fired exactly once (two completions total) and the reply was recovered.
    assert mock_create.await_count == 2, "empty finish_reason=length must trigger a retry"
    assert reply == "Привет! Расскажу подробнее про завтрак 18 июля."

    # The retry escalated reasoning_effort + token budget.
    retry_kwargs = mock_create.await_args_list[1].kwargs
    assert retry_kwargs["reasoning_effort"] == AI_REASONING_EFFORT_RETRY
    assert retry_kwargs["max_completion_tokens"] == AI_MAX_COMPLETION_TOKENS_RETRY


async def test_empty_length_both_calls_returns_none_not_crash(
    monkeypatch, async_db_session, test_workspace, test_agent_factory,
):
    """If the retry is ALSO empty, return None gracefully (logged, not crashed).
    No infinite retry loop — exactly two completions."""
    monkeypatch.setattr(ai_engine.settings, "openai_model", "gpt-5-mini-2025-08-07")
    agent = await test_agent_factory(name="Empty Twice Agent")

    empty = make_openai_response(text_content="", finish_reason="length")
    mock_create = AsyncMock(side_effect=[empty, empty])

    with patch.object(ai_engine.client.chat.completions, "create", new=mock_create):
        reply = await ai_engine.ai_engine.generate_response(
            session=async_db_session,
            conversation_id=str(_uuid.uuid4()),
            context_id=str(agent.id),
            contact_name="Mariya",
            new_message="Не знаю его",
        )

    assert mock_create.await_count == 2, "retry once, then stop (no loop)"
    assert reply is None


async def test_normal_stop_response_no_retry(
    monkeypatch, async_db_session, test_workspace, test_agent_factory,
):
    """A normal finish_reason='stop' reply is returned as-is with no retry."""
    monkeypatch.setattr(ai_engine.settings, "openai_model", "gpt-5-mini-2025-08-07")
    agent = await test_agent_factory(name="Normal Agent")

    ok = make_openai_response(text_content="Как дела?", finish_reason="stop")
    mock_create = AsyncMock(side_effect=[ok])

    with patch.object(ai_engine.client.chat.completions, "create", new=mock_create):
        reply = await ai_engine.ai_engine.generate_response(
            session=async_db_session,
            conversation_id=str(_uuid.uuid4()),
            context_id=str(agent.id),
            contact_name="Ivan",
            new_message="Привет",
        )

    assert mock_create.await_count == 1, "no retry on a normal reply"
    assert reply == "Как дела?"
