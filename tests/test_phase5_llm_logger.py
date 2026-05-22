"""Phase 5 ANLX-05 — llm_logger.log_llm_call() unit tests.

Covers tests 1-4, 7, 8, 10, 11 from plan 05-03 behaviour list (happy path,
denormalisation resolve, tool_calls, error path, conv not found, FK fail,
LLMCallResponse model_validate, defensive response extraction).

Integration tests (5 + 6 + 9) live in test_phase5_llm_logger_no_block_on_error.py.
"""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.services.llm_logger import log_llm_call

pytestmark = pytest.mark.asyncio


def _mock_openai_response(
    content: str = "AI reply",
    tool_calls=None,
    prompt_tokens: int = 100,
    completion_tokens: int = 50,
):
    """Build a MagicMock that quacks like an OpenAI ChatCompletion response."""
    resp = MagicMock()
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls
    resp.choices = [MagicMock(message=msg)]
    if prompt_tokens is not None:
        usage = MagicMock()
        usage.prompt_tokens = prompt_tokens
        usage.completion_tokens = completion_tokens
        usage.total_tokens = prompt_tokens + completion_tokens
        resp.usage = usage
    else:
        resp.usage = None
    return resp


# ── Test 1: happy path ────────────────────────────────────────────────────────


async def test_log_llm_call_happy_path(
    async_db_session, test_conversation_factory
):
    conv = await test_conversation_factory()
    response = _mock_openai_response(
        content="Hello!", prompt_tokens=200, completion_tokens=30,
    )
    prompt = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": "You are an assistant"},
            {"role": "user", "content": "Hi"},
        ],
        "temperature": 0.7,
    }

    await log_llm_call(
        workspace_id=conv["workspace_id"],
        conversation_id=conv["id"],
        model="gpt-4o-mini",
        prompt=prompt,
        response=response,
        latency_ms=150,
        error=None,
    )

    row = (await async_db_session.execute(text("""
        SELECT workspace_id, conversation_id, model, response_text,
               prompt_tokens, completion_tokens, total_tokens, latency_ms, error
        FROM llm_calls WHERE conversation_id = :cid
    """), {"cid": str(conv["id"])})).first()
    assert row is not None
    assert str(row.workspace_id) == str(conv["workspace_id"])
    assert row.model == "gpt-4o-mini"
    assert row.response_text == "Hello!"
    assert row.prompt_tokens == 200
    assert row.completion_tokens == 30
    assert row.total_tokens == 230
    assert row.latency_ms == 150
    assert row.error is None


# ── Test 2: denormalisation resolve ───────────────────────────────────────────


async def test_log_llm_call_resolves_denormalised_cols(
    async_db_session,
    test_conversation_factory,
    test_campaign_factory,
    test_agent_factory,
    test_sender_factory,
):
    """workspace_id=None → SELECT conversations and fill camp/agent/sender."""
    sender = await test_sender_factory()
    agent = await test_agent_factory()
    camp = await test_campaign_factory()
    conv = await test_conversation_factory(
        sender=sender,
        campaign_id=camp["id"],
        ai_context_id=agent.id,
    )

    await log_llm_call(
        workspace_id=None,
        conversation_id=conv["id"],
        model="gpt-4o-mini",
        prompt={},
        response=_mock_openai_response(),
        latency_ms=10,
    )

    row = (await async_db_session.execute(text("""
        SELECT workspace_id, campaign_id, agent_id, sender_id
        FROM llm_calls WHERE conversation_id = :cid
    """), {"cid": str(conv["id"])})).first()
    assert str(row.workspace_id) == str(conv["workspace_id"])
    assert str(row.campaign_id) == str(camp["id"])
    assert str(row.agent_id) == str(agent.id)
    assert str(row.sender_id) == str(sender.id)


# ── Test 3: tool_calls extraction ─────────────────────────────────────────────


async def test_log_llm_call_with_tool_calls(
    async_db_session, test_conversation_factory
):
    conv = await test_conversation_factory()
    tc = MagicMock()
    tc.id = "call_x"
    tc.function = MagicMock()
    tc.function.name = "mark_as_lead"
    tc.function.arguments = '{"reason":"interested"}'
    response = _mock_openai_response(content=None, tool_calls=[tc])

    await log_llm_call(
        workspace_id=conv["workspace_id"],
        conversation_id=conv["id"],
        model="gpt-4o-mini",
        prompt={},
        response=response,
        latency_ms=100,
    )

    row = (await async_db_session.execute(text("""
        SELECT response_text, tool_calls FROM llm_calls
        WHERE conversation_id = :cid
    """), {"cid": str(conv["id"])})).first()
    assert row.response_text is None
    assert row.tool_calls is not None
    assert row.tool_calls[0]["name"] == "mark_as_lead"
    assert row.tool_calls[0]["id"] == "call_x"


# ── Test 4: response is None on OpenAI error ─────────────────────────────────


async def test_log_llm_call_with_none_response_and_error(
    async_db_session, test_conversation_factory
):
    conv = await test_conversation_factory()
    await log_llm_call(
        workspace_id=conv["workspace_id"],
        conversation_id=conv["id"],
        model="gpt-4o-mini",
        prompt={"messages": []},
        response=None,
        latency_ms=50,
        error="RateLimitError: 429",
    )
    row = (await async_db_session.execute(text("""
        SELECT response_text, tool_calls, prompt_tokens, completion_tokens,
               total_tokens, error
        FROM llm_calls WHERE conversation_id = :cid
    """), {"cid": str(conv["id"])})).first()
    assert row.response_text is None
    assert row.tool_calls is None
    assert row.prompt_tokens is None
    assert row.completion_tokens is None
    assert row.total_tokens is None
    assert row.error == "RateLimitError: 429"


# ── Test 7: conversation not found + workspace_id=None → skip silently ───────


async def test_log_llm_call_conversation_not_found_skips_silently(
    async_db_session, caplog
):
    """conversation_id doesn't exist + workspace_id=None → no raise, no row, warning."""
    nonexistent = uuid4()
    await log_llm_call(
        workspace_id=None,
        conversation_id=nonexistent,
        model="gpt-4o-mini",
        prompt={},
        response=_mock_openai_response(),
        latency_ms=10,
    )
    cnt = (await async_db_session.execute(text("""
        SELECT COUNT(*) FROM llm_calls WHERE conversation_id = :cid
    """), {"cid": str(nonexistent)})).scalar()
    assert cnt == 0
    assert any("workspace_id unresolved" in r.message for r in caplog.records)


# ── Test 8: explicit workspace_id, FK violation → still must not raise ───────


async def test_log_llm_call_explicit_workspace_id_with_unknown_conv(
    async_db_session, test_workspace
):
    """workspace_id provided but conversation_id is a fresh UUID → FK violates.

    The contract requires log_llm_call to NOT raise even on FK violation.
    Row count remains 0.
    """
    nonexistent_conv = uuid4()
    await log_llm_call(
        workspace_id=test_workspace.id,
        conversation_id=nonexistent_conv,
        model="gpt-4o-mini",
        prompt={},
        response=_mock_openai_response(),
        latency_ms=10,
    )
    cnt = (await async_db_session.execute(text("""
        SELECT COUNT(*) FROM llm_calls WHERE conversation_id = :cid
    """), {"cid": str(nonexistent_conv)})).scalar()
    assert cnt == 0


# ── Test 11: defensive — response.choices[0].message has no tool_calls attr ──


async def test_response_extraction_defensive_missing_tool_calls_attr(
    async_db_session, test_conversation_factory
):
    conv = await test_conversation_factory()
    response = MagicMock()
    msg = MagicMock(spec=["content"])  # spec=[] — no tool_calls attr exposed
    msg.content = "Just text"
    response.choices = [MagicMock(message=msg)]
    usage = MagicMock()
    usage.prompt_tokens = 10
    usage.completion_tokens = 5
    usage.total_tokens = 15
    response.usage = usage

    await log_llm_call(
        workspace_id=conv["workspace_id"],
        conversation_id=conv["id"],
        model="gpt-4o-mini",
        prompt={},
        response=response,
        latency_ms=10,
    )

    row = (await async_db_session.execute(text("""
        SELECT response_text, tool_calls FROM llm_calls
        WHERE conversation_id = :cid
    """), {"cid": str(conv["id"])})).first()
    assert row.response_text == "Just text"
    assert row.tool_calls is None


# ── Test 10: LLMCallResponse.model_validate from ORM row ─────────────────────


async def test_llm_call_response_pydantic_schema(
    async_db_session, test_conversation_factory
):
    from sqlalchemy import select

    from app.models import LLMCall
    from app.schemas import LLMCallResponse

    conv = await test_conversation_factory()
    await log_llm_call(
        workspace_id=conv["workspace_id"],
        conversation_id=conv["id"],
        model="gpt-4o-mini",
        prompt={"x": 1},
        response=_mock_openai_response(),
        latency_ms=20,
    )

    row = (await async_db_session.execute(
        select(LLMCall).where(LLMCall.conversation_id == conv["id"])
    )).scalar_one()
    schema = LLMCallResponse.model_validate(row)
    assert schema.model == "gpt-4o-mini"
    assert schema.latency_ms == 20
    assert schema.prompt == {"x": 1}
