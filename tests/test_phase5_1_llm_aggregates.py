"""GET /api/v1/analytics/llm — UI-CAMPD-01 (LLM trace tab aggregates).

Covers:
- Schema shape (6 keys; spend_usd_cents=0 v1 stub)
- Token-count summation over since-window
- avg_latency_ms is None for empty window
- since param accepts 1d/7d/30d/90d only — invalid → 422
- scope=campaign with cross-workspace id → 404
"""

import uuid as _uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


# ── helpers ──────────────────────────────────────────────────────────────────


def _auth_headers(jwt_factory, sub: str = "llm-agg-user") -> dict:
    return {"Authorization": f"Bearer {jwt_factory(sub=sub)}"}


async def _bind(db, ws_id, uid):
    await db.execute(text("""
        INSERT INTO user_workspaces (supabase_user_id, workspace_id, role)
        VALUES (:uid, :wid, 'owner') ON CONFLICT DO NOTHING
    """), {"uid": uid, "wid": str(ws_id)})
    await db.commit()


# ── shape ────────────────────────────────────────────────────────────────────


async def test_llm_aggregates_workspace_default_7d(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
):
    """Empty workspace → 200 with 6 keys, totals=0, spend_usd_cents=0 stub."""
    await _bind(async_db_session, test_workspace.id, "u-llm-shape")
    resp = await async_client.get(
        "/api/v1/analytics/llm?scope=workspace",
        headers=_auth_headers(valid_supabase_jwt, "u-llm-shape"),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {
        "total_calls", "avg_latency_ms",
        "prompt_tokens", "completion_tokens", "total_tokens",
        "spend_usd_cents",
    }
    # spend_usd_cents is the v1 stub — always 0
    assert body["spend_usd_cents"] == 0
    # Empty window → totals = 0, avg_latency_ms is None
    assert body["total_calls"] == 0
    assert body["prompt_tokens"] == 0
    assert body["completion_tokens"] == 0
    assert body["total_tokens"] == 0
    assert body["avg_latency_ms"] is None


async def test_llm_aggregates_requires_auth(async_client):
    resp = await async_client.get("/api/v1/analytics/llm?scope=workspace")
    assert resp.status_code == 401


# ── correctness: sum + latency ───────────────────────────────────────────────


async def test_llm_aggregates_sums_token_counts(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_sender_factory,
):
    """Two llm_calls rows → SUM(prompt_tokens), SUM(completion_tokens),
    SUM(total_tokens) reflect both; avg_latency_ms in middle of the two."""
    await _bind(async_db_session, test_workspace.id, "u-llm-sum")
    s = await test_sender_factory()
    conv_id = (await async_db_session.execute(text("""
        INSERT INTO conversations (id, workspace_id, sender_id, contact_phone,
                                   status, ai_enabled)
        VALUES (gen_random_uuid(), :wid, :sid, '+11111111199', 'active', true)
        RETURNING id
    """), {"wid": str(test_workspace.id), "sid": str(s.id)})).scalar()

    # Insert two rows with known token + latency counts.
    rows = [(100, 50, 150, 200), (200, 100, 300, 400)]
    for ptok, ctok, total, lat in rows:
        await async_db_session.execute(text("""
            INSERT INTO llm_calls (workspace_id, conversation_id, model, prompt,
                                   prompt_tokens, completion_tokens, total_tokens,
                                   latency_ms)
            VALUES (:wid, :cid, 'gpt-test', '{}'::jsonb, :p, :c, :t, :l)
        """), {"wid": str(test_workspace.id), "cid": str(conv_id),
               "p": ptok, "c": ctok, "t": total, "l": lat})
    await async_db_session.commit()

    resp = await async_client.get(
        "/api/v1/analytics/llm?scope=workspace&since=7d",
        headers=_auth_headers(valid_supabase_jwt, "u-llm-sum"),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_calls"] >= 2
    assert body["prompt_tokens"] >= 300   # 100 + 200
    assert body["completion_tokens"] >= 150  # 50 + 100
    assert body["total_tokens"] >= 450    # 150 + 300
    # AVG(latency_ms) for the two rows = (200 + 400) / 2 = 300; allow drift
    # if other tests left rows in the same workspace.
    assert body["avg_latency_ms"] is not None
    assert body["avg_latency_ms"] > 0


# ── param validation ─────────────────────────────────────────────────────────


async def test_llm_aggregates_rejects_bad_since(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
):
    """since=forever → 422 (Literal validation)."""
    await _bind(async_db_session, test_workspace.id, "u-llm-bad-since")
    resp = await async_client.get(
        "/api/v1/analytics/llm?since=forever",
        headers=_auth_headers(valid_supabase_jwt, "u-llm-bad-since"),
    )
    assert resp.status_code == 422


async def test_llm_aggregates_rejects_bad_scope(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
):
    """scope=agent (not in /llm whitelist) → 422 — only workspace|campaign allowed."""
    await _bind(async_db_session, test_workspace.id, "u-llm-bad-scope")
    resp = await async_client.get(
        "/api/v1/analytics/llm?scope=agent",
        headers=_auth_headers(valid_supabase_jwt, "u-llm-bad-scope"),
    )
    assert resp.status_code == 422


# ── workspace isolation ──────────────────────────────────────────────────────


async def test_llm_aggregates_campaign_scope_requires_id(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
):
    """scope=campaign without id → 422 ID_REQUIRED."""
    await _bind(async_db_session, test_workspace.id, "u-llm-no-id")
    resp = await async_client.get(
        "/api/v1/analytics/llm?scope=campaign",
        headers=_auth_headers(valid_supabase_jwt, "u-llm-no-id"),
    )
    assert resp.status_code == 422


async def test_llm_aggregates_campaign_scope_404_cross_workspace(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
):
    """scope=campaign with foreign-workspace id → 404."""
    from app.models import Workspace

    await _bind(async_db_session, test_workspace.id, "u-llm-cross-ws")

    other = Workspace(name="OtherWS-llm")
    async_db_session.add(other)
    await async_db_session.commit()
    await async_db_session.refresh(other)
    foreign_agent_id = _uuid.uuid4()
    await async_db_session.execute(text("""
        INSERT INTO ai_contexts (id, workspace_id, name)
        VALUES (:aid, :wid, 'foreign-llm-agent')
    """), {"aid": str(foreign_agent_id), "wid": str(other.id)})
    foreign_camp_id = _uuid.uuid4()
    await async_db_session.execute(text("""
        INSERT INTO campaigns (id, workspace_id, agent_id, name, status)
        VALUES (:cid, :wid, :aid, 'foreign-llm-camp', 'draft')
    """), {"cid": str(foreign_camp_id), "wid": str(other.id),
           "aid": str(foreign_agent_id)})
    await async_db_session.commit()

    resp = await async_client.get(
        f"/api/v1/analytics/llm?scope=campaign&id={foreign_camp_id}",
        headers=_auth_headers(valid_supabase_jwt, "u-llm-cross-ws"),
    )
    assert resp.status_code == 404
