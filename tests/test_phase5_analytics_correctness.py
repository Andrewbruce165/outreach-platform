"""Phase 5 analytics correctness (ANLX-01..04 seeded → expected).

Validates _compute_cards math with controlled fixtures:
- Test 1 (ANLX-01): workspace-level — 4 metrics correct for mixed seed
- Test 2 (Pitfall 8): bot_ignored conversations excluded from replied
- Test 3 (Pitfall 9): leads strict EQ — finished does NOT count as lead
- Test 4 (ANLX-02): campaign scope filters correctly; workspace sees both
- Test 5 (ANLX-04): agent scope filters correctly
- Test 6 (ANLX-03): sender scope filters correctly
- Test 7 (D-15): replied returns BOTH conversation_count and message_count
- Test 8 (D-16): workspace endpoint parity with per-resource shape

Uses ``test_conversation_factory`` and ``test_message_factory`` from
``tests/conftest.py`` (added in Plan 05-01) — both already pin into
``test_workspace``, so we bind the JWT user to ``test_workspace.id``.
"""

import uuid as _uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


# ── Helpers ──────────────────────────────────────────────────────────────────


def _auth_headers(jwt_factory, sub: str) -> dict:
    return {"Authorization": f"Bearer {jwt_factory(sub=sub)}"}


async def _bind(db, ws_id, uid):
    await db.execute(text("""
        INSERT INTO user_workspaces (supabase_user_id, workspace_id, role)
        VALUES (:uid, :wid, 'owner') ON CONFLICT DO NOTHING
    """), {"uid": uid, "wid": str(ws_id)})
    await db.commit()


# ── 1. ANLX-01 workspace-level: 4 metrics correct ────────────────────────────


async def test_workspace_4_metrics_correct(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory, test_message_factory,
):
    """ANLX-01 correctness — seed-and-assert all 4 metrics."""
    # Conv 1: 10 outbound messages.
    conv1 = await test_conversation_factory(status="active")
    await test_message_factory(
        conv1["id"], count=10, direction="outbound", sent_by="ai",
        workspace_id=test_workspace.id,
    )
    # Conv 2: 3 inbound messages from contact (replied).
    conv2 = await test_conversation_factory(status="active")
    await test_message_factory(
        conv2["id"], count=3, direction="inbound", sent_by="contact",
        workspace_id=test_workspace.id,
    )
    # Conv 3: 2 inbound messages from contact (replied).
    conv3 = await test_conversation_factory(status="active")
    await test_message_factory(
        conv3["id"], count=2, direction="inbound", sent_by="contact",
        workspace_id=test_workspace.id,
    )
    # Plus 1 lead and 1 finished conversation (no messages — leads/finishes
    # counted from conversations table directly).
    await test_conversation_factory(status="lead")
    await test_conversation_factory(status="finished")

    await _bind(async_db_session, test_workspace.id, "u-ws-4m")
    r = await async_client.get(
        "/api/v1/analytics/workspace",
        headers=_auth_headers(valid_supabase_jwt, "u-ws-4m"),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["sent"] == 10
    assert data["replied"]["conversation_count"] == 2
    assert data["replied"]["message_count"] == 5
    assert data["leads"] == 1
    assert data["finishes"] == 1


# ── 2. Pitfall 8: bot_ignored excluded from replied ──────────────────────────


async def test_bot_ignored_excluded_from_replied(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory, test_message_factory,
):
    """Pitfall 8 — bot conversations НЕ учитываются в replied counts."""
    # Bot conv: 5 inbound (should NOT be counted).
    bot_conv = await test_conversation_factory(
        status="bot_ignored", ai_enabled=False,
    )
    await test_message_factory(
        bot_conv["id"], count=5, direction="inbound", sent_by="contact",
        workspace_id=test_workspace.id,
    )
    # Real conv: 3 inbound (should be counted).
    real_conv = await test_conversation_factory(status="active")
    await test_message_factory(
        real_conv["id"], count=3, direction="inbound", sent_by="contact",
        workspace_id=test_workspace.id,
    )

    await _bind(async_db_session, test_workspace.id, "u-bot-excl")
    r = await async_client.get(
        "/api/v1/analytics/workspace",
        headers=_auth_headers(valid_supabase_jwt, "u-bot-excl"),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    # Only real conv counted: 1 conversation, 3 messages.
    assert data["replied"]["conversation_count"] == 1
    assert data["replied"]["message_count"] == 3


# ── 3. Pitfall 9: leads strict EQ (no finished included) ─────────────────────


async def test_leads_mutually_exclusive_with_finished(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory,
):
    """Pitfall 9 — leads=COUNT WHERE status='lead' (НЕ включает 'finished').

    Per D-16 verbatim. UI label: «Активные лиды (ещё не финишировали)».
    """
    await test_conversation_factory(status="lead")
    await test_conversation_factory(status="finished")
    await _bind(async_db_session, test_workspace.id, "u-leads-mut")

    r = await async_client.get(
        "/api/v1/analytics/workspace",
        headers=_auth_headers(valid_supabase_jwt, "u-leads-mut"),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["leads"] == 1  # not 2 — finished не считается как lead
    assert data["finishes"] == 1


# ── 4. ANLX-02: campaign scope filtering ─────────────────────────────────────


async def test_campaign_scope_filters(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory, test_conversation_factory, test_message_factory,
):
    """ANLX-02 — campaign endpoint фильтрует по c.campaign_id."""
    camp = await test_campaign_factory()
    # Conv attached to campaign — 5 outbound.
    conv_in = await test_conversation_factory(
        campaign_id=camp["id"], status="active",
    )
    await test_message_factory(
        conv_in["id"], count=5, direction="outbound", sent_by="ai",
        workspace_id=test_workspace.id,
    )
    # Conv NOT attached to campaign — 3 outbound.
    conv_out = await test_conversation_factory(status="active")
    await test_message_factory(
        conv_out["id"], count=3, direction="outbound", sent_by="ai",
        workspace_id=test_workspace.id,
    )
    await _bind(async_db_session, test_workspace.id, "u-camp-scope")

    h = _auth_headers(valid_supabase_jwt, "u-camp-scope")
    # Campaign endpoint sees only conv_in (5 outbound).
    r_camp = await async_client.get(
        f"/api/v1/analytics/campaigns/{camp['id']}", headers=h,
    )
    assert r_camp.status_code == 200, r_camp.text
    assert r_camp.json()["sent"] == 5

    # Workspace endpoint sees both (5 + 3 = 8).
    r_ws = await async_client.get("/api/v1/analytics/workspace", headers=h)
    assert r_ws.status_code == 200, r_ws.text
    assert r_ws.json()["sent"] == 8


# ── 5. ANLX-04: agent scope filtering ────────────────────────────────────────


async def test_agent_scope_filters(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_agent_factory, test_conversation_factory, test_message_factory,
):
    """ANLX-04 — agent endpoint фильтрует по c.ai_context_id."""
    agent = await test_agent_factory()
    conv_in = await test_conversation_factory(
        ai_context_id=agent.id, status="active",
    )
    await test_message_factory(
        conv_in["id"], count=4, direction="outbound", sent_by="ai",
        workspace_id=test_workspace.id,
    )
    conv_out = await test_conversation_factory(status="active")
    await test_message_factory(
        conv_out["id"], count=2, direction="outbound", sent_by="ai",
        workspace_id=test_workspace.id,
    )
    await _bind(async_db_session, test_workspace.id, "u-agent-scope")

    h = _auth_headers(valid_supabase_jwt, "u-agent-scope")
    r = await async_client.get(
        f"/api/v1/analytics/agents/{agent.id}", headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["sent"] == 4

    r_ws = await async_client.get("/api/v1/analytics/workspace", headers=h)
    assert r_ws.json()["sent"] == 6


# ── 6. ANLX-03: sender scope filtering ───────────────────────────────────────


async def test_sender_scope_filters(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_sender_factory, test_conversation_factory, test_message_factory,
):
    """ANLX-03 — sender endpoint фильтрует по c.sender_id."""
    sender_a = await test_sender_factory()
    sender_b = await test_sender_factory()
    conv_a = await test_conversation_factory(sender=sender_a, status="active")
    await test_message_factory(
        conv_a["id"], count=7, direction="outbound", sent_by="ai",
        workspace_id=test_workspace.id,
    )
    conv_b = await test_conversation_factory(sender=sender_b, status="active")
    await test_message_factory(
        conv_b["id"], count=2, direction="outbound", sent_by="ai",
        workspace_id=test_workspace.id,
    )
    await _bind(async_db_session, test_workspace.id, "u-sender-scope")

    h = _auth_headers(valid_supabase_jwt, "u-sender-scope")
    r_a = await async_client.get(
        f"/api/v1/analytics/senders/{sender_a.id}", headers=h,
    )
    assert r_a.status_code == 200, r_a.text
    assert r_a.json()["sent"] == 7

    r_b = await async_client.get(
        f"/api/v1/analytics/senders/{sender_b.id}", headers=h,
    )
    assert r_b.status_code == 200, r_b.text
    assert r_b.json()["sent"] == 2

    # Workspace-level — sum across all senders.
    r_ws = await async_client.get("/api/v1/analytics/workspace", headers=h)
    assert r_ws.json()["sent"] == 9


# ── 7. D-15: replied returns both figures ────────────────────────────────────


async def test_replied_two_figures(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory, test_message_factory,
):
    """D-15: replied returns BOTH conversation_count + message_count.

    Seeded 2 conversations × 3 inbound messages each → conv_count=2, msg_count=6.
    """
    c1 = await test_conversation_factory(status="active")
    await test_message_factory(
        c1["id"], count=3, direction="inbound", sent_by="contact",
        workspace_id=test_workspace.id,
    )
    c2 = await test_conversation_factory(status="active")
    await test_message_factory(
        c2["id"], count=3, direction="inbound", sent_by="contact",
        workspace_id=test_workspace.id,
    )
    await _bind(async_db_session, test_workspace.id, "u-replied-2fig")

    r = await async_client.get(
        "/api/v1/analytics/workspace",
        headers=_auth_headers(valid_supabase_jwt, "u-replied-2fig"),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["replied"]["conversation_count"] == 2
    assert data["replied"]["message_count"] == 6


# ── 8. T-05-02-WS-ISOLATION (correctness): cross-workspace data invisible ────


async def test_workspace_isolation_in_all_4_counts(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory, test_message_factory,
):
    """Each of the 4 COUNT'ов internally has WHERE c.workspace_id=:wid.

    Even if a foreign workspace has heavy data, user sees only own counts.
    """
    from app.models import Sender, Workspace

    # Workspace A: 2 outbound + 1 inbound + 1 lead.
    a_conv = await test_conversation_factory(status="active")
    await test_message_factory(
        a_conv["id"], count=2, direction="outbound", sent_by="ai",
        workspace_id=test_workspace.id,
    )
    await test_message_factory(
        a_conv["id"], count=1, direction="inbound", sent_by="contact",
        workspace_id=test_workspace.id,
    )
    await test_conversation_factory(status="lead")
    await _bind(async_db_session, test_workspace.id, "u-iso-correctness")

    # Workspace B: 50 outbound + 50 inbound + 50 leads + 50 finishes.
    other = Workspace(name="OtherWS-iso-counts")
    async_db_session.add(other)
    await async_db_session.commit()
    await async_db_session.refresh(other)
    b_sender = Sender(
        workspace_id=other.id, slug="iso-counts-sender", name="ISO",
        phone="+79002223334", session_string="x", role="sender",
        lifecycle_status="active", auth_status="ok",
    )
    async_db_session.add(b_sender)
    await async_db_session.commit()
    await async_db_session.refresh(b_sender)
    b_conv_id = _uuid.uuid4()
    await async_db_session.execute(text("""
        INSERT INTO conversations (id, workspace_id, sender_id, contact_phone, status)
        VALUES (:cid, :wid, :sid, '+79992224445', 'active')
    """), {"cid": str(b_conv_id), "wid": str(other.id), "sid": str(b_sender.id)})
    await async_db_session.commit()
    # 50 outbound + 50 inbound in workspace B.
    for i in range(50):
        await async_db_session.execute(text("""
            INSERT INTO messages
                (workspace_id, conversation_id, direction, message_text,
                 sent_by, telegram_message_id)
            VALUES (:wid, :cid, 'outbound', 'B-out', 'ai', :tmid)
        """), {"wid": str(other.id), "cid": str(b_conv_id), "tmid": 800_000 + i})
    for i in range(50):
        await async_db_session.execute(text("""
            INSERT INTO messages
                (workspace_id, conversation_id, direction, message_text,
                 sent_by, telegram_message_id)
            VALUES (:wid, :cid, 'inbound', 'B-in', 'contact', :tmid)
        """), {"wid": str(other.id), "cid": str(b_conv_id), "tmid": 700_000 + i})
    # 50 leads + 50 finishes in workspace B.
    for i in range(50):
        await async_db_session.execute(text("""
            INSERT INTO conversations (id, workspace_id, sender_id, contact_phone, status)
            VALUES (:cid, :wid, :sid, :phone, 'lead')
        """), {
            "cid": str(_uuid.uuid4()), "wid": str(other.id),
            "sid": str(b_sender.id), "phone": f"+7999000{i:04d}",
        })
    for i in range(50):
        await async_db_session.execute(text("""
            INSERT INTO conversations (id, workspace_id, sender_id, contact_phone, status)
            VALUES (:cid, :wid, :sid, :phone, 'finished')
        """), {
            "cid": str(_uuid.uuid4()), "wid": str(other.id),
            "sid": str(b_sender.id), "phone": f"+7999100{i:04d}",
        })
    await async_db_session.commit()

    # User in workspace A sees ONLY own counts.
    r = await async_client.get(
        "/api/v1/analytics/workspace",
        headers=_auth_headers(valid_supabase_jwt, "u-iso-correctness"),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["sent"] == 2
    assert data["replied"]["conversation_count"] == 1
    assert data["replied"]["message_count"] == 1
    assert data["leads"] == 1
    assert data["finishes"] == 0


# ── 9. internal/warmup exclusion: contact == own sender НЕ считается ──────────


async def test_internal_warmup_conversation_excluded(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_sender_factory, test_conversation_factory, test_message_factory,
):
    """Диалог, где contact_telegram_id == telegram_id НАШЕГО sender'а — это
    warmup/internal трафик между своими аккаунтами, НЕ реальный аутрич.

    Регрессия на инцидент 2026-06-23/24: warmup-переписка между нашими
    аккаунтами протекла в conversations/messages и раздула sent/replied вдвое
    (см. .planning/debug/dashboard-analytics-warmup-pollution.md). Фильтр
    _EXCLUDE_INTERNAL_CLAUSE в analytics.py обязан её отсекать во всех метриках.
    """
    # Наш собственный sender с известным telegram_id (он же — "контакт" warmup).
    own_sender = await test_sender_factory(
        slug="warmup-peer", telegram_id=555_111_222,
    )

    # Реальный внешний диалог: 4 outbound + 2 inbound (учитывается).
    real_conv = await test_conversation_factory(
        status="active", contact_telegram_id=999_888_777,
    )
    await test_message_factory(
        real_conv["id"], count=4, direction="outbound", sent_by="ai",
        workspace_id=test_workspace.id,
    )
    await test_message_factory(
        real_conv["id"], count=2, direction="inbound", sent_by="contact",
        workspace_id=test_workspace.id,
    )

    # Internal/warmup диалог: contact_telegram_id == own_sender.telegram_id.
    # 30 outbound + 30 inbound — НЕ должны попасть ни в одну метрику.
    warmup_conv = await test_conversation_factory(
        status="active", contact_telegram_id=own_sender.telegram_id,
    )
    await test_message_factory(
        warmup_conv["id"], count=30, direction="outbound", sent_by="human",
        workspace_id=test_workspace.id,
    )
    await test_message_factory(
        warmup_conv["id"], count=30, direction="inbound", sent_by="contact",
        workspace_id=test_workspace.id,
    )

    await _bind(async_db_session, test_workspace.id, "u-warmup-excl")
    h = _auth_headers(valid_supabase_jwt, "u-warmup-excl")

    # Cards: только реальный диалог (warmup отфильтрован).
    r = await async_client.get("/api/v1/analytics/workspace", headers=h)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["sent"] == 4, "warmup outbound must be excluded"
    assert data["replied"]["conversation_count"] == 1
    assert data["replied"]["message_count"] == 2

    # Funnel: те же стадии тоже исключают warmup.
    rf = await async_client.get("/api/v1/analytics/funnel", headers=h)
    assert rf.status_code == 200, rf.text
    funnel = rf.json()
    assert funnel["sent"] == 4
    assert funnel["replied"] == 1
