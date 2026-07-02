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


# ── spend: real per-model pricing (ILX-LLM-SPEND) ────────────────────────────


async def test_llm_aggregates_real_spend_known_model(
    async_client, valid_supabase_jwt, async_db_session, test_sender_factory,
):
    """Known priced model → spend_usd_cents == compute_spend_cents of the rows.

    Isolated in a fresh workspace so the exact-equality assertion is not
    polluted by rows other tests left in the shared workspace.
    """
    from app.models import Workspace
    from app.services.llm_pricing import compute_spend_cents

    ws = Workspace(name="ws-llm-spend-known")
    async_db_session.add(ws)
    await async_db_session.commit()
    await async_db_session.refresh(ws)
    await _bind(async_db_session, ws.id, "u-llm-spend-known")

    s = await test_sender_factory()
    conv_id = (await async_db_session.execute(text("""
        INSERT INTO conversations (id, workspace_id, sender_id, contact_phone,
                                   status, ai_enabled)
        VALUES (gen_random_uuid(), :wid, :sid, '+11111111177', 'active', true)
        RETURNING id
    """), {"wid": str(ws.id), "sid": str(s.id)})).scalar()

    rows = [("gpt-4o-mini", 1_000_000, 500_000),
            ("gpt-4o-mini", 2_000_000, 1_000_000)]
    for model, ptok, ctok in rows:
        await async_db_session.execute(text("""
            INSERT INTO llm_calls (workspace_id, conversation_id, model, prompt,
                                   prompt_tokens, completion_tokens, total_tokens,
                                   latency_ms)
            VALUES (:wid, :cid, :m, '{}'::jsonb, :p, :c, :t, 100)
        """), {"wid": str(ws.id), "cid": str(conv_id), "m": model,
               "p": ptok, "c": ctok, "t": ptok + ctok})
    await async_db_session.commit()

    expected = compute_spend_cents([(m, p, c) for m, p, c in rows])
    assert expected > 0  # sanity: gpt-4o-mini is priced

    resp = await async_client.get(
        "/api/v1/analytics/llm?scope=workspace&since=7d",
        headers=_auth_headers(valid_supabase_jwt, "u-llm-spend-known"),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["spend_usd_cents"] == expected


async def test_llm_aggregates_unknown_model_zero_spend_no_crash(
    async_client, valid_supabase_jwt, async_db_session, test_sender_factory,
):
    """Unknown model string → spend_usd_cents == 0, endpoint still 200."""
    from app.models import Workspace

    ws = Workspace(name="ws-llm-spend-unknown")
    async_db_session.add(ws)
    await async_db_session.commit()
    await async_db_session.refresh(ws)
    await _bind(async_db_session, ws.id, "u-llm-spend-unknown")

    s = await test_sender_factory()
    conv_id = (await async_db_session.execute(text("""
        INSERT INTO conversations (id, workspace_id, sender_id, contact_phone,
                                   status, ai_enabled)
        VALUES (gen_random_uuid(), :wid, :sid, '+11111111166', 'active', true)
        RETURNING id
    """), {"wid": str(ws.id), "sid": str(s.id)})).scalar()

    await async_db_session.execute(text("""
        INSERT INTO llm_calls (workspace_id, conversation_id, model, prompt,
                               prompt_tokens, completion_tokens, total_tokens,
                               latency_ms)
        VALUES (:wid, :cid, 'totally-unpriced-model', '{}'::jsonb,
                9_000_000, 9_000_000, 18_000_000, 100)
    """), {"wid": str(ws.id), "cid": str(conv_id)})
    await async_db_session.commit()

    resp = await async_client.get(
        "/api/v1/analytics/llm?scope=workspace&since=7d",
        headers=_auth_headers(valid_supabase_jwt, "u-llm-spend-unknown"),
    )
    assert resp.status_code == 200
    assert resp.json()["spend_usd_cents"] == 0


async def test_workspace_analytics_exposes_llm_spend_field(
    async_client, valid_supabase_jwt, async_db_session, test_sender_factory,
):
    """GET /analytics/workspace carries additive llm_spend_usd_cents > 0 when
    priced rows exist (all-time, no since-window)."""
    from app.models import Workspace

    ws = Workspace(name="ws-cards-llm-spend")
    async_db_session.add(ws)
    await async_db_session.commit()
    await async_db_session.refresh(ws)
    await _bind(async_db_session, ws.id, "u-cards-llm-spend")

    s = await test_sender_factory()
    conv_id = (await async_db_session.execute(text("""
        INSERT INTO conversations (id, workspace_id, sender_id, contact_phone,
                                   status, ai_enabled)
        VALUES (gen_random_uuid(), :wid, :sid, '+11111111155', 'active', true)
        RETURNING id
    """), {"wid": str(ws.id), "sid": str(s.id)})).scalar()

    await async_db_session.execute(text("""
        INSERT INTO llm_calls (workspace_id, conversation_id, model, prompt,
                               prompt_tokens, completion_tokens, total_tokens,
                               latency_ms)
        VALUES (:wid, :cid, 'gpt-5-mini-2025-08-07', '{}'::jsonb,
                1_000_000, 1_000_000, 2_000_000, 100)
    """), {"wid": str(ws.id), "cid": str(conv_id)})
    await async_db_session.commit()

    resp = await async_client.get(
        "/api/v1/analytics/workspace",
        headers=_auth_headers(valid_supabase_jwt, "u-cards-llm-spend"),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "llm_spend_usd_cents" in body  # additive field present
    # gpt-5-mini prefix: 0.25 + 2.00 USD/1M = 225 cents for 1M+1M.
    assert body["llm_spend_usd_cents"] == 225


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
