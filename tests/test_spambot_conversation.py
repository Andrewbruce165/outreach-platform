"""Integration tests for the per-sender @SpamBot live-chat conversation
(quick task 260713-hiw).

Covers the get-or-create endpoint backing the account-page "Text to SpamBot"
side panel:
  1. POST /senders/{slug}/spambot-conversation → 200 status='spambot' + id;
     idempotent (second call returns the SAME conversation id).
  2. The created conversation is ai_enabled=false and is EXCLUDED from the
     default Inbox list (GET /conversations with no status filter).
  3. Workspace-scoped: a slug from another workspace → 404 (never 403).
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


# ─── Helpers (mirror tests/test_senders.py) ──────────────────────────────────


async def _create_workspace_via_jwt(async_client, valid_supabase_jwt, sub: str):
    """Bootstrap a workspace via JWT POST /auth/me. Returns (token, workspace_id)."""
    token = valid_supabase_jwt(sub=sub, email=f"{sub}@test.com")
    r = await async_client.post(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    return token, r.json()["workspace_id"]


async def _insert_sender_raw(
    db: AsyncSession,
    workspace_id: str,
    slug: str,
) -> str:
    """Direct INSERT into senders. Returns sender_id."""
    sid = str(uuid.uuid4())
    await db.execute(
        text("""
            INSERT INTO senders
                (id, workspace_id, slug, name, phone, session_string, role,
                 lifecycle_status, auth_status, rate_per_min, rate_per_hour)
            VALUES
                (:id, :wid, :slug, :name, :phone, 'encrypted_stub', 'sender',
                 'active', 'ok', 4, 20)
        """),
        {
            "id": sid, "wid": workspace_id, "slug": slug, "name": slug,
            "phone": f"+7900{sid[:7]}",
        },
    )
    await db.commit()
    return sid


# ─── 1: get-or-create + idempotency ──────────────────────────────────────────


async def test_spambot_conversation_get_or_create_idempotent(
    async_client, async_db_session, valid_supabase_jwt
):
    token, ws = await _create_workspace_via_jwt(
        async_client, valid_supabase_jwt, sub="sb-idem"
    )
    await _insert_sender_raw(async_db_session, ws, "sb-idem-sender")

    r1 = await async_client.post(
        "/api/v1/senders/sb-idem-sender/spambot-conversation",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r1.status_code == 200, r1.text
    body1 = r1.json()
    assert body1["status"] == "spambot"
    assert body1["conversation_id"]

    # Second call must return the SAME conversation (get-or-create idempotency).
    r2 = await async_client.post(
        "/api/v1/senders/sb-idem-sender/spambot-conversation",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["conversation_id"] == body1["conversation_id"]


# ─── 2: ai_enabled=false + excluded from Inbox ────────────────────────────────


async def test_spambot_conversation_ai_disabled_and_hidden_from_inbox(
    async_client, async_db_session, valid_supabase_jwt
):
    token, ws = await _create_workspace_via_jwt(
        async_client, valid_supabase_jwt, sub="sb-hidden"
    )
    await _insert_sender_raw(async_db_session, ws, "sb-hidden-sender")

    r = await async_client.post(
        "/api/v1/senders/sb-hidden-sender/spambot-conversation",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    conv_id = r.json()["conversation_id"]

    # ai_enabled=false + status='spambot' persisted.
    row = (await async_db_session.execute(text("""
        SELECT ai_enabled, status, contact_telegram_id, contact_phone
        FROM conversations WHERE id = :cid
    """), {"cid": conv_id})).fetchone()
    assert row is not None
    assert row.ai_enabled is False
    assert row.status == "spambot"
    assert row.contact_telegram_id == 178220800
    assert row.contact_phone == "spambot:178220800"

    # Absent from the default Inbox list (status=None → excludes 'spambot').
    lr = await async_client.get(
        "/api/v1/conversations",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert lr.status_code == 200, lr.text
    ids = [c["id"] for c in lr.json()["conversations"]]
    assert conv_id not in ids


# ─── 3: workspace isolation ───────────────────────────────────────────────────


async def test_spambot_conversation_cross_tenant_returns_404(
    async_client, async_db_session, valid_supabase_jwt
):
    token_a, _ws_a = await _create_workspace_via_jwt(
        async_client, valid_supabase_jwt, sub="sb-cross-a"
    )
    _token_b, ws_b = await _create_workspace_via_jwt(
        async_client, valid_supabase_jwt, sub="sb-cross-b"
    )
    # Sender lives in workspace B; A must not reach it.
    await _insert_sender_raw(async_db_session, ws_b, "sb-cross-b-sender")

    r = await async_client.post(
        "/api/v1/senders/sb-cross-b-sender/spambot-conversation",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "SENDER_NOT_FOUND"
