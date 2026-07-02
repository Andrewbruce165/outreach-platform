"""Phase 5 analytics cards `?since=` window filter (KF2-SINCE-01).

The 4 cards-endpoints (workspace / campaigns / agents / senders) gain an
optional ``?since=1d|7d|30d|90d`` query-param. When omitted the response is
byte-identical to the current all-time behaviour (regression). When present it
temporally filters the period-sensitive metrics:

- sent + replied      → filtered by ``messages.created_at``
- leads + finishes     → filtered by ``conversations.updated_at``
- llm_spend_usd_cents  → filtered by ``llm_calls.created_at``
- contacts_messaged / registered_contacts → NEVER filtered (campaign progress,
  numerator/denominator, period-agnostic).

Backdating uses raw UPDATE (conftest factories don't accept custom timestamps).
Test window is ``since=7d`` with stale rows aged 40 days → reliably outside.

Each workspace bind uses its own JWT sub (avoids user_workspaces UNIQUE).
Each scenario seeds into its own campaign / conversation so shared-workspace
row pollution cannot leak across assertions.
"""

import uuid as _uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


# ── helpers (verbatim from test_phase5_analytics.py) ──────────────────────────


def _auth_headers(jwt_factory, sub: str = "analytics-since-user") -> dict:
    return {"Authorization": f"Bearer {jwt_factory(sub=sub)}"}


async def _bind(db, ws_id, uid):
    await db.execute(text("""
        INSERT INTO user_workspaces (supabase_user_id, workspace_id, role)
        VALUES (:uid, :wid, 'owner') ON CONFLICT DO NOTHING
    """), {"uid": uid, "wid": str(ws_id)})
    await db.commit()


async def _backdate_messages(db, conv_id, days=40):
    await db.execute(
        text("UPDATE messages SET created_at = NOW() - (:d || ' days')::INTERVAL "
             "WHERE conversation_id = :cid"),
        {"d": str(days), "cid": str(conv_id)},
    )
    await db.commit()


async def _backdate_conversation(db, conv_id, days=40):
    await db.execute(
        text("UPDATE conversations SET updated_at = NOW() - (:d || ' days')::INTERVAL "
             "WHERE id = :cid"),
        {"d": str(days), "cid": str(conv_id)},
    )
    await db.commit()


async def _backdate_llm_calls(db, conv_id, days=40):
    await db.execute(
        text("UPDATE llm_calls SET created_at = NOW() - (:d || ' days')::INTERVAL "
             "WHERE conversation_id = :cid"),
        {"d": str(days), "cid": str(conv_id)},
    )
    await db.commit()


# ── regression: since omitted == all-time ─────────────────────────────────────


async def test_no_since_counts_all_time_stale_rows(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory, test_message_factory,
):
    """Without ?since, a 40-day-old outbound+inbound pair is still counted."""
    await _bind(async_db_session, test_workspace.id, "u-since-regress")
    conv = await test_conversation_factory(status="active")
    await test_message_factory(
        conv["id"], count=1, direction="outbound", sent_by="ai",
        workspace_id=test_workspace.id,
    )
    await test_message_factory(
        conv["id"], count=1, direction="inbound", sent_by="contact",
        workspace_id=test_workspace.id,
    )
    await _backdate_messages(async_db_session, conv["id"], days=40)

    r = await async_client.get(
        "/api/v1/analytics/workspace",
        headers=_auth_headers(valid_supabase_jwt, "u-since-regress"),
    )
    assert r.status_code == 200
    data = r.json()
    assert data["sent"] == 1
    assert data["replied"]["conversation_count"] == 1
    assert data["replied"]["message_count"] == 1


# ── since window: sent/replied by messages.created_at ─────────────────────────


async def test_since_excludes_stale_messages(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory, test_message_factory,
):
    """?since=7d hides messages older than the window (40 days → sent/replied 0)."""
    await _bind(async_db_session, test_workspace.id, "u-since-stale-msg")
    conv = await test_conversation_factory(status="active")
    await test_message_factory(
        conv["id"], count=1, direction="outbound", sent_by="ai",
        workspace_id=test_workspace.id,
    )
    await test_message_factory(
        conv["id"], count=1, direction="inbound", sent_by="contact",
        workspace_id=test_workspace.id,
    )
    await _backdate_messages(async_db_session, conv["id"], days=40)

    r = await async_client.get(
        "/api/v1/analytics/workspace?since=7d",
        headers=_auth_headers(valid_supabase_jwt, "u-since-stale-msg"),
    )
    assert r.status_code == 200
    data = r.json()
    assert data["sent"] == 0
    assert data["replied"]["conversation_count"] == 0
    assert data["replied"]["message_count"] == 0


async def test_since_includes_fresh_messages(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory, test_message_factory,
):
    """?since=7d counts messages created now (inside the window)."""
    await _bind(async_db_session, test_workspace.id, "u-since-fresh-msg")
    conv = await test_conversation_factory(status="active")
    await test_message_factory(
        conv["id"], count=1, direction="outbound", sent_by="ai",
        workspace_id=test_workspace.id,
    )
    await test_message_factory(
        conv["id"], count=1, direction="inbound", sent_by="contact",
        workspace_id=test_workspace.id,
    )
    # No backdate — created_at = now.

    r = await async_client.get(
        "/api/v1/analytics/workspace?since=7d",
        headers=_auth_headers(valid_supabase_jwt, "u-since-fresh-msg"),
    )
    assert r.status_code == 200
    data = r.json()
    assert data["sent"] == 1
    assert data["replied"]["conversation_count"] == 1


# ── since window: leads/finishes by conversations.updated_at ──────────────────


async def test_since_excludes_stale_leads_and_finishes(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory,
):
    """Lead/finished conversations with updated_at 40d ago → ?since=7d gives 0."""
    await _bind(async_db_session, test_workspace.id, "u-since-stale-lead")
    lead_conv = await test_conversation_factory(status="lead")
    fin_conv = await test_conversation_factory(status="finished")
    await _backdate_conversation(async_db_session, lead_conv["id"], days=40)
    await _backdate_conversation(async_db_session, fin_conv["id"], days=40)

    r = await async_client.get(
        "/api/v1/analytics/workspace?since=7d",
        headers=_auth_headers(valid_supabase_jwt, "u-since-stale-lead"),
    )
    assert r.status_code == 200
    data = r.json()
    assert data["leads"] == 0
    assert data["finishes"] == 0


async def test_since_includes_fresh_leads_and_finishes(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_conversation_factory,
):
    """Fresh lead/finished conversations counted inside the window."""
    await _bind(async_db_session, test_workspace.id, "u-since-fresh-lead")
    await test_conversation_factory(status="lead")
    await test_conversation_factory(status="finished")
    # No backdate.

    r = await async_client.get(
        "/api/v1/analytics/workspace?since=7d",
        headers=_auth_headers(valid_supabase_jwt, "u-since-fresh-lead"),
    )
    assert r.status_code == 200
    data = r.json()
    assert data["leads"] == 1
    assert data["finishes"] == 1


# ── since window: llm_spend by llm_calls.created_at (campaign scope) ───────────


async def test_since_excludes_stale_llm_spend(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory, test_conversation_factory,
):
    """Priced llm_calls backdated 40d → ?since=7d gives llm_spend_usd_cents=0."""
    await _bind(async_db_session, test_workspace.id, "u-since-stale-llm")
    camp = await test_campaign_factory()
    conv = await test_conversation_factory(status="active", campaign_id=camp["id"])
    for _ in range(2):
        await async_db_session.execute(text("""
            INSERT INTO llm_calls (workspace_id, conversation_id, campaign_id, model,
                                   prompt, prompt_tokens, completion_tokens,
                                   total_tokens, latency_ms)
            VALUES (:wid, :cid, :camp, 'gpt-4o-mini', '{}'::jsonb,
                    1000000, 1000000, 2000000, 100)
        """), {"wid": str(test_workspace.id), "cid": str(conv["id"]),
               "camp": str(camp["id"])})
    await async_db_session.commit()
    await _backdate_llm_calls(async_db_session, conv["id"], days=40)

    r = await async_client.get(
        f"/api/v1/analytics/campaigns/{camp['id']}?since=7d",
        headers=_auth_headers(valid_supabase_jwt, "u-since-stale-llm"),
    )
    assert r.status_code == 200
    assert r.json()["llm_spend_usd_cents"] == 0


async def test_since_includes_fresh_llm_spend(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory, test_conversation_factory,
):
    """Fresh priced llm_calls counted inside the window (spend > 0)."""
    await _bind(async_db_session, test_workspace.id, "u-since-fresh-llm")
    camp = await test_campaign_factory()
    conv = await test_conversation_factory(status="active", campaign_id=camp["id"])
    await async_db_session.execute(text("""
        INSERT INTO llm_calls (workspace_id, conversation_id, campaign_id, model,
                               prompt, prompt_tokens, completion_tokens,
                               total_tokens, latency_ms)
        VALUES (:wid, :cid, :camp, 'gpt-4o-mini', '{}'::jsonb,
                1000000, 1000000, 2000000, 100)
    """), {"wid": str(test_workspace.id), "cid": str(conv["id"]),
           "camp": str(camp["id"])})
    await async_db_session.commit()
    # No backdate.

    r = await async_client.get(
        f"/api/v1/analytics/campaigns/{camp['id']}?since=7d",
        headers=_auth_headers(valid_supabase_jwt, "u-since-fresh-llm"),
    )
    assert r.status_code == 200
    assert r.json()["llm_spend_usd_cents"] > 0


# ── contacts_messaged NOT filtered by period ──────────────────────────────────


async def test_contacts_messaged_not_filtered_by_since(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory, test_conversation_factory, test_message_factory,
):
    """contacts_messaged (campaign progress numerator) ignores the since-window:
    stale outbound (40d) still contributes even with ?since=7d."""
    await _bind(async_db_session, test_workspace.id, "u-since-progress")
    camp = await test_campaign_factory()
    conv = await test_conversation_factory(status="active", campaign_id=camp["id"])
    await test_message_factory(
        conv["id"], count=1, direction="outbound", sent_by="ai",
        workspace_id=test_workspace.id,
    )
    await _backdate_messages(async_db_session, conv["id"], days=40)

    r = await async_client.get(
        f"/api/v1/analytics/campaigns/{camp['id']}?since=7d",
        headers=_auth_headers(valid_supabase_jwt, "u-since-progress"),
    )
    assert r.status_code == 200
    data = r.json()
    # Period does NOT apply to progress numerator — stale outbound still counts.
    assert data["contacts_messaged"] == 1
    # But period-sensitive `sent` IS filtered out (same stale rows).
    assert data["sent"] == 0


# ── smoke: all 4 endpoints accept ?since ──────────────────────────────────────


async def test_all_4_endpoints_accept_since(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory, test_agent_factory, test_sender_factory,
):
    """Each of the 4 cards-endpoints returns 200 for ?since=30d."""
    await _bind(async_db_session, test_workspace.id, "u-since-smoke")
    h = _auth_headers(valid_supabase_jwt, "u-since-smoke")
    camp = await test_campaign_factory()
    agent = await test_agent_factory()
    sender = await test_sender_factory()

    paths = [
        "/api/v1/analytics/workspace?since=30d",
        f"/api/v1/analytics/campaigns/{camp['id']}?since=30d",
        f"/api/v1/analytics/agents/{agent.id}?since=30d",
        f"/api/v1/analytics/senders/{sender.id}?since=30d",
    ]
    for p in paths:
        r = await async_client.get(p, headers=h)
        assert r.status_code == 200, f"{p} status={r.status_code} body={r.text}"


async def test_bad_since_rejected(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
):
    """?since=forever → 422 (Literal validation)."""
    await _bind(async_db_session, test_workspace.id, "u-since-bad")
    r = await async_client.get(
        "/api/v1/analytics/workspace?since=forever",
        headers=_auth_headers(valid_supabase_jwt, "u-since-bad"),
    )
    assert r.status_code == 422
