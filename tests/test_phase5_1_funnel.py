"""GET /api/v1/analytics/funnel — UI-DASH-01 (Sankey funnel 5-stage).

Covers:
- Schema shape (5 stage keys)
- Monotonic non-increasing assertion on seeded data
- engaged-stage definition LOCKED per RESEARCH.md Pitfall 5
- bot_ignored conversations excluded from every count (Phase 5 Pitfall 8)
- Cross-workspace 404 + invalid-scope 422 + missing-id 422
"""

import uuid as _uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


# ── helpers ──────────────────────────────────────────────────────────────────


def _auth_headers(jwt_factory, sub: str = "funnel-user") -> dict:
    return {"Authorization": f"Bearer {jwt_factory(sub=sub)}"}


async def _bind(db, ws_id, uid):
    """Insert user_workspaces row so auth_dep resolves the test user → ws."""
    await db.execute(text("""
        INSERT INTO user_workspaces (supabase_user_id, workspace_id, role)
        VALUES (:uid, :wid, 'owner') ON CONFLICT DO NOTHING
    """), {"uid": uid, "wid": str(ws_id)})
    await db.commit()


# ── shape + auth ─────────────────────────────────────────────────────────────


async def test_funnel_workspace_scope_returns_five_stages(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
):
    """Body shape: exactly {sent, replied, engaged, lead, handoff}."""
    await _bind(async_db_session, test_workspace.id, "u-funnel-shape")
    resp = await async_client.get(
        "/api/v1/analytics/funnel?scope=workspace",
        headers=_auth_headers(valid_supabase_jwt, "u-funnel-shape"),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"sent", "replied", "engaged", "lead", "handoff"}
    for k in ("sent", "replied", "engaged", "lead", "handoff"):
        assert isinstance(body[k], int)
        assert body[k] >= 0


async def test_funnel_workspace_requires_auth(async_client):
    """Без Authorization → 401."""
    resp = await async_client.get("/api/v1/analytics/funnel?scope=workspace")
    assert resp.status_code == 401


# ── monotonicity + correctness ───────────────────────────────────────────────


async def test_funnel_stages_monotonic_non_increasing(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_sender_factory,
):
    """Stage counts non-increasing: sent >= replied >= engaged on a seed
    designed to satisfy that invariant (every replied conv has >= 1 outbound,
    every engaged conv has >= 2 inbound)."""
    await _bind(async_db_session, test_workspace.id, "u-funnel-monotonic")
    s = await test_sender_factory()

    # 3 conversations, all status='active' (so engaged stage is reachable).
    # Each gets 1 outbound message + variable inbound counts.
    # convs[0]: 2 inbound → engaged
    # convs[1]: 1 inbound → replied (but not engaged)
    # convs[2]: 0 inbound → sent only
    conv_ids = []
    for i in range(3):
        cid = (await async_db_session.execute(text("""
            INSERT INTO conversations (id, workspace_id, sender_id, contact_phone,
                                       status, ai_enabled)
            VALUES (gen_random_uuid(), :wid, :sid, :phone, 'active', true)
            RETURNING id
        """), {"wid": str(test_workspace.id), "sid": str(s.id),
               "phone": f"+1900000010{i}"})).scalar()
        conv_ids.append(cid)
        # 1 outbound for each
        await async_db_session.execute(text("""
            INSERT INTO messages (workspace_id, conversation_id, direction,
                                  message_text, sent_by, telegram_message_id)
            VALUES (:wid, :cid, 'outbound', 'hello', 'ai', :tmid)
        """), {"wid": str(test_workspace.id), "cid": str(cid), "tmid": 700_000 + i})

    # convs[0] gets 2 inbound (engaged)
    for j in range(2):
        await async_db_session.execute(text("""
            INSERT INTO messages (workspace_id, conversation_id, direction,
                                  message_text, sent_by, telegram_message_id)
            VALUES (:wid, :cid, 'inbound', :txt, 'contact', :tmid)
        """), {"wid": str(test_workspace.id), "cid": str(conv_ids[0]),
               "txt": f"reply{j}", "tmid": 710_000 + j})
    # convs[1] gets 1 inbound (replied, not engaged)
    await async_db_session.execute(text("""
        INSERT INTO messages (workspace_id, conversation_id, direction,
                              message_text, sent_by, telegram_message_id)
        VALUES (:wid, :cid, 'inbound', 'hi', 'contact', :tmid)
    """), {"wid": str(test_workspace.id), "cid": str(conv_ids[1]), "tmid": 720_000})
    await async_db_session.commit()

    resp = await async_client.get(
        "/api/v1/analytics/funnel?scope=workspace",
        headers=_auth_headers(valid_supabase_jwt, "u-funnel-monotonic"),
    )
    assert resp.status_code == 200
    body = resp.json()
    # sent = 3 outbound; replied (distinct convs with >=1 inbound) = 2;
    # engaged (>=2 inbound) = 1; lead/handoff = 0.
    assert body["sent"] >= 3
    assert body["replied"] >= 2
    assert body["engaged"] >= 1
    # Strict monotonic check across the chain.
    assert body["sent"] >= body["replied"], body
    assert body["replied"] >= body["engaged"], body


async def test_funnel_engaged_definition_locked(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_sender_factory,
):
    """Engaged = >= 2 inbound contact messages AND status NOT IN terminal/bot.

    Insert one active conv with 2 inbound contact msgs; expect engaged>=1.
    """
    await _bind(async_db_session, test_workspace.id, "u-funnel-engaged-def")
    s = await test_sender_factory()
    conv_id = (await async_db_session.execute(text("""
        INSERT INTO conversations (id, workspace_id, sender_id, contact_phone,
                                   status, ai_enabled)
        VALUES (gen_random_uuid(), :wid, :sid, '+11234567890', 'active', true)
        RETURNING id
    """), {"wid": str(test_workspace.id), "sid": str(s.id)})).scalar()
    for i in range(2):
        await async_db_session.execute(text("""
            INSERT INTO messages (conversation_id, workspace_id, direction,
                                  message_text, sent_by, telegram_message_id)
            VALUES (:cid, :wid, 'inbound', :txt, 'contact', :tmid)
        """), {"cid": str(conv_id), "wid": str(test_workspace.id),
               "txt": f"msg{i}", "tmid": 800_000 + i})
    await async_db_session.commit()

    resp = await async_client.get(
        "/api/v1/analytics/funnel?scope=workspace",
        headers=_auth_headers(valid_supabase_jwt, "u-funnel-engaged-def"),
    )
    body = resp.json()
    assert body["engaged"] >= 1, (
        f"Expected engaged>=1 with 1 active conv + 2 inbound msgs, got {body}"
    )


async def test_funnel_excludes_bot_ignored(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_sender_factory,
):
    """bot_ignored conversations contribute 0 to every stage (Pitfall 8)."""
    await _bind(async_db_session, test_workspace.id, "u-funnel-bot-ignored")
    s = await test_sender_factory()

    # Baseline funnel snapshot (probably zeros for this user/ws).
    resp_before = await async_client.get(
        "/api/v1/analytics/funnel?scope=workspace",
        headers=_auth_headers(valid_supabase_jwt, "u-funnel-bot-ignored"),
    )
    before = resp_before.json()

    # Insert a conv with status='bot_ignored' + 5 inbound + 3 outbound msgs.
    conv_id = (await async_db_session.execute(text("""
        INSERT INTO conversations (id, workspace_id, sender_id, contact_phone,
                                   status, ai_enabled)
        VALUES (gen_random_uuid(), :wid, :sid, '+10000000099', 'bot_ignored', false)
        RETURNING id
    """), {"wid": str(test_workspace.id), "sid": str(s.id)})).scalar()
    for i in range(3):
        await async_db_session.execute(text("""
            INSERT INTO messages (workspace_id, conversation_id, direction,
                                  message_text, sent_by, telegram_message_id)
            VALUES (:wid, :cid, 'outbound', 'spam', 'ai', :tmid)
        """), {"wid": str(test_workspace.id), "cid": str(conv_id), "tmid": 850_000 + i})
    for i in range(5):
        await async_db_session.execute(text("""
            INSERT INTO messages (workspace_id, conversation_id, direction,
                                  message_text, sent_by, telegram_message_id)
            VALUES (:wid, :cid, 'inbound', 'reply', 'contact', :tmid)
        """), {"wid": str(test_workspace.id), "cid": str(conv_id), "tmid": 860_000 + i})
    await async_db_session.commit()

    resp_after = await async_client.get(
        "/api/v1/analytics/funnel?scope=workspace",
        headers=_auth_headers(valid_supabase_jwt, "u-funnel-bot-ignored"),
    )
    after = resp_after.json()
    # Every count must be unchanged — bot_ignored conv is filtered out.
    assert after["sent"] == before["sent"], (before, after)
    assert after["replied"] == before["replied"], (before, after)
    assert after["engaged"] == before["engaged"], (before, after)
    assert after["lead"] == before["lead"], (before, after)
    assert after["handoff"] == before["handoff"], (before, after)


# ── scope guards ─────────────────────────────────────────────────────────────


async def test_funnel_campaign_scope_requires_id(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
):
    """scope=campaign without id → 422 ID_REQUIRED."""
    await _bind(async_db_session, test_workspace.id, "u-funnel-no-id")
    resp = await async_client.get(
        "/api/v1/analytics/funnel?scope=campaign",
        headers=_auth_headers(valid_supabase_jwt, "u-funnel-no-id"),
    )
    assert resp.status_code == 422


async def test_funnel_rejects_invalid_scope(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
):
    """scope=bogus → 422 (FastAPI Literal validation)."""
    await _bind(async_db_session, test_workspace.id, "u-funnel-bad-scope")
    resp = await async_client.get(
        "/api/v1/analytics/funnel?scope=bogus",
        headers=_auth_headers(valid_supabase_jwt, "u-funnel-bad-scope"),
    )
    assert resp.status_code == 422


async def test_funnel_campaign_scope_404_cross_workspace(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
):
    """scope=campaign with foreign-workspace id → 404."""
    from app.models import Workspace

    await _bind(async_db_session, test_workspace.id, "u-funnel-cross-ws")

    # Create a foreign workspace + a foreign agent + campaign id we never bind.
    other = Workspace(name="OtherWS-funnel")
    async_db_session.add(other)
    await async_db_session.commit()
    await async_db_session.refresh(other)
    foreign_agent_id = _uuid.uuid4()
    await async_db_session.execute(text("""
        INSERT INTO ai_contexts (id, workspace_id, name)
        VALUES (:aid, :wid, 'foreign-funnel-agent')
    """), {"aid": str(foreign_agent_id), "wid": str(other.id)})
    foreign_camp_id = _uuid.uuid4()
    await async_db_session.execute(text("""
        INSERT INTO campaigns (id, workspace_id, agent_id, name, status)
        VALUES (:cid, :wid, :aid, 'foreign-funnel-camp', 'draft')
    """), {"cid": str(foreign_camp_id), "wid": str(other.id),
           "aid": str(foreign_agent_id)})
    await async_db_session.commit()

    resp = await async_client.get(
        f"/api/v1/analytics/funnel?scope=campaign&id={foreign_camp_id}",
        headers=_auth_headers(valid_supabase_jwt, "u-funnel-cross-ws"),
    )
    assert resp.status_code == 404
