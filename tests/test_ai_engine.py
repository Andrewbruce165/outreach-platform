"""Phase 3 — ai_engine.get_context adapter (RESEARCH Pitfall 1).

After migration 015 dropped is_active / max_message_length / webhook_functions
from ai_contexts, get_context() must not SELECT them — it provides defaults
so build_system_prompt + build_tools keep working unchanged.
"""
import pytest

pytestmark = pytest.mark.asyncio


async def test_get_context_phase3_schema(async_db_session, test_agent_factory):
    """После миграции 015 get_context работает без is_active/max_message_length/webhook_functions."""
    from app.services.ai_engine import ai_engine

    agent = await test_agent_factory(
        name="Phase 3 Agent",
        system_prompt="test prompt",
        tone_of_voice="friendly",
        rules="rule 1",
        company_info="Test Co.",
    )
    # Clear cache to force DB hit
    ai_engine._context_cache.clear()

    ctx = await ai_engine.get_context(async_db_session, str(agent.id))

    assert ctx["system_prompt"] == "test prompt"
    assert ctx["tone_of_voice"] == "friendly"
    assert ctx["rules"] == "rule 1"
    assert ctx["company_info"] == "Test Co."
    # Phase 3: defaults because columns dropped
    assert ctx["max_message_length"] == 500
    assert ctx["webhook_functions"] == []


async def test_get_context_returns_defaults_for_missing(async_db_session):
    """Несуществующий context_id → default_context, без SQL ошибок."""
    from app.services.ai_engine import ai_engine

    ai_engine._context_cache.clear()
    ctx = await ai_engine.get_context(async_db_session, "00000000-0000-0000-0000-000000000000")

    assert ctx["max_message_length"] == 500
    assert ctx["webhook_functions"] == []
