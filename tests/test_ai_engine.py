"""Phase 3 — ai_engine.get_context adapter (RESEARCH Pitfall 1).

После миграции 015 в ai_contexts больше нет is_active / max_message_length /
webhook_functions — get_context() не должен их SELECTить и проставляет
max_message_length дефолтом, чтобы build_system_prompt работал без правок.

Phase 5 (SaaS-чистка): убран DEFAULT_SYSTEM_PROMPT-fallback и поле
webhook_functions из возвращаемого dict-а. Несуществующий context_id и
пустой context_id теперь возвращают None — workspace обязан настроить агента.
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

    assert ctx is not None
    assert ctx["system_prompt"] == "test prompt"
    assert ctx["tone_of_voice"] == "friendly"
    assert ctx["rules"] == "rule 1"
    assert ctx["company_info"] == "Test Co."
    # Phase 05.1: колонка вернулась миграцией 018 (default 280) — get_context
    # теперь читает её из БД и прокидывает в build_system_prompt → <message_style>.
    assert ctx["max_message_length"] == 280
    # webhook_functions выпилен из возвращаемого dict-а
    assert "webhook_functions" not in ctx


async def test_get_context_returns_none_for_missing(async_db_session):
    """Несуществующий context_id → None (брендового fallback больше нет)."""
    from app.services.ai_engine import ai_engine

    ai_engine._context_cache.clear()
    ctx = await ai_engine.get_context(async_db_session, "00000000-0000-0000-0000-000000000000")

    assert ctx is None


async def test_get_context_returns_none_for_empty_id(async_db_session):
    """Пустой context_id → None, без обращения к БД."""
    from app.services.ai_engine import ai_engine

    ctx = await ai_engine.get_context(async_db_session, None)
    assert ctx is None

    ctx = await ai_engine.get_context(async_db_session, "")
    assert ctx is None
