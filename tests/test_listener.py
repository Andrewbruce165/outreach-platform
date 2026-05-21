"""Phase 3 — listener.get_active_senders adapter (RESEARCH Pitfall 5).

After migration 015 dropped senders.ai_context_id, get_active_senders() must
not SELECT or return it — agent_id will come from conversation.campaign_id
JOIN in Phase 4.
"""
import pytest

pytestmark = pytest.mark.asyncio


async def test_get_active_senders_no_ai_context_id(async_db_session, test_sender_factory):
    """Phase 3 D-04: get_active_senders больше не SELECT'ит ai_context_id из senders."""
    from app.services.listener import TelegramListener

    await test_sender_factory(slug="test-active-sender", lifecycle_status="active", auth_status="ok")

    listener = TelegramListener()
    senders = await listener.get_active_senders()

    assert isinstance(senders, list)
    assert len(senders) >= 1
    # Phase 3: every returned dict MUST NOT have 'ai_context_id' key
    for s in senders:
        assert "ai_context_id" not in s, \
            f"sender dict still has 'ai_context_id' key (got: {list(s.keys())})"
        # required keys still present
        assert "id" in s
        assert "slug" in s
        assert "session_string" in s
        assert "proxy" in s
