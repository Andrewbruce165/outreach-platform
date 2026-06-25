"""Phase 4 Plan 04-05 Task 2 — GREEN tests for campaign-level webhook
fire-and-forget (notify_signal helper).

Closes CAMP-14 (3 webhook URLs) test surface.
"""

import asyncio
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text as _t

from app.services.ai_engine import _handle_builtin_signal
from app.services.webhook_notify import notify_signal

pytestmark = pytest.mark.asyncio


# ─── Helper: stub asyncio.create_task to await the coroutine inline ───────────


class _ImmediateTask:
    """asyncio.create_task replacement that awaits inline for tests."""

    def __init__(self):
        self.awaited = []

    def __call__(self, coro):
        # Await the coroutine inline — the test then immediately sees the
        # side effects (httpx mock invocation) without needing event-loop wait.
        # We can't await here (not an async function), so schedule and remember.
        loop = asyncio.get_event_loop()
        fut = loop.create_task(coro)
        self.awaited.append(fut)
        return fut


# ─── Tests ─────────────────────────────────────────────────────────────────────


async def test_lead_webhook_called_on_mark_as_lead(
    async_db_session, test_workspace, test_sender_factory, test_campaign_factory
):
    """campaign.lead_webhook_url='http://...' — POST'ится при mark_as_lead."""
    sender = await test_sender_factory()
    camp = await test_campaign_factory(lead_webhook_url="https://example.com/lead-hook")
    conv_id = uuid.uuid4()
    await async_db_session.execute(
        _t(
            """
            INSERT INTO conversations
                (id, workspace_id, sender_id, campaign_id, contact_phone, ai_enabled, status)
            VALUES (:id, :wid, :sid, :cid, :phone, true, 'active')
            """
        ),
        {
            "id": str(conv_id),
            "wid": str(test_workspace.id),
            "sid": str(sender.id),
            "cid": str(camp["id"]),
            "phone": "+79220000001",
        },
    )
    await async_db_session.commit()

    posted_urls = []

    async def _post_mock(self, url, json=None, **kw):
        posted_urls.append(url)

        class R:
            status_code = 200

        return R()

    with patch("httpx.AsyncClient.post", new=_post_mock):
        await _handle_builtin_signal(
            db=async_db_session,
            conversation_id=conv_id,
            campaign={
                "id": camp["id"],
                "name": camp["name"],
                "workspace_id": camp["workspace_id"],
                "lead_webhook_url": "https://example.com/lead-hook",
            },
            contact={"phone": "+79220000001", "full_name": "X"},
            signal_name="mark_as_lead",
            reason="готов покупать",
        )
        # let fire-and-forget task run
        await asyncio.sleep(0.05)

    assert "https://example.com/lead-hook" in posted_urls


async def test_handoff_webhook_called_on_transfer_to_manager(
    async_db_session, test_workspace, test_sender_factory, test_campaign_factory
):
    sender = await test_sender_factory()
    camp = await test_campaign_factory(
        handoff_webhook_url="https://example.com/handoff-hook"
    )
    conv_id = uuid.uuid4()
    await async_db_session.execute(
        _t(
            """
            INSERT INTO conversations
                (id, workspace_id, sender_id, campaign_id, contact_phone, ai_enabled, status)
            VALUES (:id, :wid, :sid, :cid, :phone, true, 'active')
            """
        ),
        {
            "id": str(conv_id),
            "wid": str(test_workspace.id),
            "sid": str(sender.id),
            "cid": str(camp["id"]),
            "phone": "+79220000002",
        },
    )
    await async_db_session.commit()

    posted_urls = []

    async def _post_mock(self, url, json=None, **kw):
        posted_urls.append(url)

        class R:
            status_code = 200

        return R()

    with patch("httpx.AsyncClient.post", new=_post_mock):
        await _handle_builtin_signal(
            db=async_db_session,
            conversation_id=conv_id,
            campaign={
                "id": camp["id"],
                "name": camp["name"],
                "workspace_id": camp["workspace_id"],
                "handoff_webhook_url": "https://example.com/handoff-hook",
            },
            contact={"phone": "+79220000002"},
            signal_name="transfer_to_manager",
            reason="нужен менеджер",
        )
        await asyncio.sleep(0.05)

    assert "https://example.com/handoff-hook" in posted_urls


async def test_finish_webhook_called_on_finish_conversation(
    async_db_session, test_workspace, test_sender_factory, test_campaign_factory
):
    sender = await test_sender_factory()
    camp = await test_campaign_factory(
        finish_webhook_url="https://example.com/finish-hook"
    )
    conv_id = uuid.uuid4()
    await async_db_session.execute(
        _t(
            """
            INSERT INTO conversations
                (id, workspace_id, sender_id, campaign_id, contact_phone, ai_enabled, status)
            VALUES (:id, :wid, :sid, :cid, :phone, true, 'active')
            """
        ),
        {
            "id": str(conv_id),
            "wid": str(test_workspace.id),
            "sid": str(sender.id),
            "cid": str(camp["id"]),
            "phone": "+79220000003",
        },
    )
    await async_db_session.commit()

    posted_urls = []

    async def _post_mock(self, url, json=None, **kw):
        posted_urls.append(url)

        class R:
            status_code = 200

        return R()

    with patch("httpx.AsyncClient.post", new=_post_mock):
        await _handle_builtin_signal(
            db=async_db_session,
            conversation_id=conv_id,
            campaign={
                "id": camp["id"],
                "name": camp["name"],
                "workspace_id": camp["workspace_id"],
                "finish_webhook_url": "https://example.com/finish-hook",
            },
            contact={"phone": "+79220000003"},
            signal_name="finish_conversation",
            reason="до свидания",
        )
        await asyncio.sleep(0.05)

    assert "https://example.com/finish-hook" in posted_urls


async def test_null_webhook_url_no_error_status_still_updated(
    async_db_session, test_workspace, test_sender_factory, test_campaign_factory
):
    """CAMP-14: lead_webhook_url=NULL — webhook не вызывается, но conversation.status='lead'."""
    sender = await test_sender_factory()
    camp = await test_campaign_factory(lead_webhook_url=None)
    conv_id = uuid.uuid4()
    await async_db_session.execute(
        _t(
            """
            INSERT INTO conversations
                (id, workspace_id, sender_id, campaign_id, contact_phone, ai_enabled, status)
            VALUES (:id, :wid, :sid, :cid, :phone, true, 'active')
            """
        ),
        {
            "id": str(conv_id),
            "wid": str(test_workspace.id),
            "sid": str(sender.id),
            "cid": str(camp["id"]),
            "phone": "+79220000004",
        },
    )
    await async_db_session.commit()

    posted_urls = []

    async def _post_mock(self, url, json=None, **kw):
        posted_urls.append(url)

        class R:
            status_code = 200

        return R()

    with patch("httpx.AsyncClient.post", new=_post_mock):
        status = await _handle_builtin_signal(
            db=async_db_session,
            conversation_id=conv_id,
            campaign={
                "id": camp["id"],
                "name": camp["name"],
                "workspace_id": camp["workspace_id"],
                "lead_webhook_url": None,
            },
            contact={"phone": "+79220000004"},
            signal_name="mark_as_lead",
            reason="interest",
        )
        await asyncio.sleep(0.05)

    # status updated even when URL is NULL
    assert status == "lead"
    row = (
        await async_db_session.execute(
            _t("SELECT status FROM conversations WHERE id = :id"), {"id": str(conv_id)}
        )
    ).first()
    assert row.status == "lead"

    # no webhook posted
    assert posted_urls == []


async def test_webhook_payload_shape_correct(
    async_db_session, test_workspace, test_sender_factory, test_campaign_factory
):
    """Payload содержит: event_type, campaign_id, campaign_name, conversation_id,
    workspace_id, contact{phone,name,username,source,custom,telegram_id}, reason,
    message_history_excerpt, timestamp."""
    sender = await test_sender_factory()
    camp = await test_campaign_factory(lead_webhook_url="https://example.com/hook")
    conv_id = uuid.uuid4()
    await async_db_session.execute(
        _t(
            """
            INSERT INTO conversations
                (id, workspace_id, sender_id, campaign_id, contact_phone, ai_enabled, status)
            VALUES (:id, :wid, :sid, :cid, :phone, true, 'active')
            """
        ),
        {
            "id": str(conv_id),
            "wid": str(test_workspace.id),
            "sid": str(sender.id),
            "cid": str(camp["id"]),
            "phone": "+79220000005",
        },
    )
    await async_db_session.commit()

    captured = {}

    async def _post_mock(self, url, json=None, **kw):
        captured["url"] = url
        captured["payload"] = json

        class R:
            status_code = 200

        return R()

    with patch("httpx.AsyncClient.post", new=_post_mock):
        await notify_signal(
            event_type="lead",
            campaign={
                "id": camp["id"],
                "name": camp["name"],
                "workspace_id": camp["workspace_id"],
                "lead_webhook_url": "https://example.com/hook",
            },
            conversation_id=conv_id,
            contact={
                "phone": "+79220000005",
                "telegram_id": 12345,
                "full_name": "Иван",
                "username": "ivan_test",
                "source": "csv-2025-05",
                "custom": {"company": "ООО Тест"},
            },
            reason="клиент готов",
            db=async_db_session,
        )
        await asyncio.sleep(0.05)

    p = captured["payload"]
    assert p["event_type"] == "lead"
    assert p["campaign_id"] == str(camp["id"])
    assert p["campaign_name"] == camp["name"]
    assert p["conversation_id"] == str(conv_id)
    assert p["workspace_id"] == str(camp["workspace_id"])
    assert p["contact"]["phone"] == "+79220000005"
    assert p["contact"]["telegram_id"] == 12345
    assert p["contact"]["name"] == "Иван"
    assert p["contact"]["username"] == "ivan_test"
    assert p["contact"]["source"] == "csv-2025-05"
    assert p["contact"]["custom"] == {"company": "ООО Тест"}
    assert p["reason"] == "клиент готов"
    assert "message_history_excerpt" in p
    assert isinstance(p["message_history_excerpt"], list)
    assert "timestamp" in p


async def test_webhook_fire_and_forget_does_not_block_ai_response(
    async_db_session, test_workspace, test_sender_factory, test_campaign_factory
):
    """Если webhook URL отвечает 30s — AI response не задерживается (asyncio.create_task)."""
    sender = await test_sender_factory()
    camp = await test_campaign_factory(lead_webhook_url="https://example.com/slow")
    conv_id = uuid.uuid4()
    await async_db_session.execute(
        _t(
            """
            INSERT INTO conversations
                (id, workspace_id, sender_id, campaign_id, contact_phone, ai_enabled, status)
            VALUES (:id, :wid, :sid, :cid, :phone, true, 'active')
            """
        ),
        {
            "id": str(conv_id),
            "wid": str(test_workspace.id),
            "sid": str(sender.id),
            "cid": str(camp["id"]),
            "phone": "+79220000006",
        },
    )
    await async_db_session.commit()

    async def _slow_post(self, url, json=None, **kw):
        await asyncio.sleep(2.0)  # would block 2s if not fire-and-forget

        class R:
            status_code = 200

        return R()

    import time as _time

    with patch("httpx.AsyncClient.post", new=_slow_post):
        t0 = _time.monotonic()
        await notify_signal(
            event_type="lead",
            campaign={
                "id": camp["id"],
                "name": camp["name"],
                "workspace_id": camp["workspace_id"],
                "lead_webhook_url": "https://example.com/slow",
            },
            conversation_id=conv_id,
            contact={"phone": "+79220000006"},
            reason="x",
            db=async_db_session,
        )
        elapsed = _time.monotonic() - t0

    # Should return well under 1 second — actual post happens in background task.
    assert elapsed < 1.0, f"notify_signal blocked for {elapsed:.2f}s (expected <1s)"


async def test_message_history_excerpt_last_20(
    async_db_session, test_workspace, test_sender_factory, test_campaign_factory
):
    """In payload — последние 20 сообщений диалога (chronologically asc)."""
    sender = await test_sender_factory()
    camp = await test_campaign_factory(lead_webhook_url="https://example.com/hist")
    conv_id = uuid.uuid4()
    await async_db_session.execute(
        _t(
            """
            INSERT INTO conversations
                (id, workspace_id, sender_id, campaign_id, contact_phone, ai_enabled, status)
            VALUES (:id, :wid, :sid, :cid, :phone, true, 'active')
            """
        ),
        {
            "id": str(conv_id),
            "wid": str(test_workspace.id),
            "sid": str(sender.id),
            "cid": str(camp["id"]),
            "phone": "+79220000007",
        },
    )

    # 25 messages — should only get the last 20 in excerpt.
    for i in range(25):
        direction = "inbound" if i % 2 == 0 else "outbound"
        await async_db_session.execute(
            _t(
                """
                INSERT INTO messages
                    (workspace_id, conversation_id, direction, message_text, sent_by, created_at)
                VALUES (:wid, :cid, :dir, :txt, :sent_by, NOW() + (:i || ' seconds')::interval)
                """
            ),
            {
                "wid": str(test_workspace.id),
                "cid": str(conv_id),
                "dir": direction,
                "txt": f"msg {i}",
                "sent_by": "contact" if direction == "inbound" else "ai",
                "i": str(i),
            },
        )
    await async_db_session.commit()

    captured = {}

    async def _post_mock(self, url, json=None, **kw):
        captured["payload"] = json

        class R:
            status_code = 200

        return R()

    with patch("httpx.AsyncClient.post", new=_post_mock):
        await notify_signal(
            event_type="lead",
            campaign={
                "id": camp["id"],
                "name": camp["name"],
                "workspace_id": camp["workspace_id"],
                "lead_webhook_url": "https://example.com/hist",
            },
            conversation_id=conv_id,
            contact={"phone": "+79220000007"},
            reason="x",
            db=async_db_session,
        )
        await asyncio.sleep(0.1)

    excerpt = captured["payload"]["message_history_excerpt"]
    assert len(excerpt) == 20  # cap at MESSAGE_HISTORY_LIMIT
    # chronologically asc — last 20 are msgs 5..24
    assert excerpt[0]["content"] == "msg 5"
    assert excerpt[-1]["content"] == "msg 24"
