"""Phase 10 — Pool-health + per-sender enrichment tests (Wave 0 RED stubs).

These tests fully ASSERT the `pool_health` aggregate and the per-sender
`restriction_status`/`restricted_until` enrichment that Plan 10-03 will add to
`_campaign_to_response` (CampaignResponse). The fields do not exist yet, so the
tests FAIL RED at run time while `--collect-only` stays clean — the expected
Wave-0 state per 10-VALIDATION.md (pattern mirrors tests/test_pool_endpoints.py).

The campaign is fetched through the real GET endpoint
(`GET /api/v1/campaigns/{id}`) so `pool_health` and the enriched `attached_senders`
are exercised through the production `_campaign_to_response` path.

pool_health shape (10-RESEARCH.md:280, 10-01-PLAN <interfaces>):
    {active: int, paused: int, total: int, earliest_resume_at: datetime | None}
The green/yellow/red badge is derived ON THE FRONTEND — there is no badge_state
field in the API (D — numeric pool_health only, presentation-free).

Test → requirement map (contract — names consumed by later verify commands):
- test_pool_health_states        → POOLV-01 (3-state arithmetic: all-active / partial / all-paused)
- test_attached_senders_enriched → POOLV-02 (attached_senders[] carry restriction_status/restricted_until)
"""

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


def _auth_headers(jwt_factory, sub: str) -> dict:
    return {"Authorization": f"Bearer {jwt_factory(sub=sub)}"}


async def _bind(db, ws_id, uid):
    """Bind a Supabase user (JWT sub) to a workspace as owner.

    Each test uses a DISTINCT sub (user_workspaces has UNIQUE(supabase_user_id),
    migration 023; session-scoped schema) — same convention as test_pool_endpoints.
    """
    await db.execute(text("""
        INSERT INTO user_workspaces (supabase_user_id, workspace_id, role)
        VALUES (:uid, :wid, 'owner') ON CONFLICT DO NOTHING
    """), {"uid": uid, "wid": str(ws_id)})
    await db.commit()


async def _freeze_sender(db, sender_id, status: str = "spam_limited", until=None):
    """Force a sender into a restricted state (mirror tests/test_failover.py:62-70).

    `until` defaults to NOW()+24h; pass an explicit timestamp to drive the
    earliest_resume_at MIN() assertion.
    """
    if until is None:
        await db.execute(text("""
            UPDATE senders
            SET restriction_status = :st, restricted_until = NOW() + INTERVAL '24 hours'
            WHERE id = :sid
        """), {"st": status, "sid": str(sender_id)})
    else:
        await db.execute(text("""
            UPDATE senders SET restriction_status = :st, restricted_until = :until
            WHERE id = :sid
        """), {"st": status, "until": until, "sid": str(sender_id)})
    await db.commit()


# ─── POOLV-01 ────────────────────────────────────────────────────────────────

async def test_pool_health_states(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_running_campaign_factory,
):
    """POOLV-01: pool_health numeric contract across the three pool states.

    (a) all active   → {active:3, paused:0, total:3, earliest_resume_at:None}
    (b) one frozen   → {active:2, paused:1, total:3, earliest_resume_at:T}
    (c) all frozen   → {active:0, paused:3, total:3, earliest_resume_at:MIN(...)}
    """
    sub = "pool-health"
    await _bind(async_db_session, test_workspace.id, sub)
    camp, senders = await test_running_campaign_factory(sender_count=3)
    headers = _auth_headers(valid_supabase_jwt, sub)

    async def _pool_health():
        r = await async_client.get(f"/api/v1/campaigns/{camp['id']}", headers=headers)
        assert r.status_code == 200, r.text
        return r.json()["pool_health"]

    # (a) all active. has_backup True (quick-260706-c1p: >=2 sendable senders).
    ph = await _pool_health()
    assert ph == {"active": 3, "paused": 0, "total": 3, "earliest_resume_at": None,
                  "has_backup": True}, ph

    # (b) freeze one with a known release date.
    t1 = (await async_db_session.execute(
        text("SELECT NOW() + INTERVAL '6 hours'")
    )).scalar_one()
    await _freeze_sender(async_db_session, senders[0].id, "spam_limited", until=t1)
    ph = await _pool_health()
    assert ph["active"] == 2, ph
    assert ph["paused"] == 1, ph
    assert ph["total"] == 3, ph
    assert ph["earliest_resume_at"] is not None, ph
    # 2 sendable senders remain → still has a backup (advisory True).
    assert ph["has_backup"] is True, ph

    # (c) freeze all three with distinct release dates → earliest_resume_at = MIN.
    t_min = (await async_db_session.execute(
        text("SELECT NOW() + INTERVAL '2 hours'")
    )).scalar_one()
    t_max = (await async_db_session.execute(
        text("SELECT NOW() + INTERVAL '10 hours'")
    )).scalar_one()
    await _freeze_sender(async_db_session, senders[1].id, "spam_limited", until=t_min)
    await _freeze_sender(async_db_session, senders[2].id, "frozen", until=t_max)
    ph = await _pool_health()
    assert ph["active"] == 0, ph
    assert ph["paused"] == 3, ph
    assert ph["total"] == 3, ph
    # 0 sendable senders → no backup (advisory False).
    assert ph["has_backup"] is False, ph
    # The earliest of {t1, t_min, t_max} is t_min (2h).
    assert ph["earliest_resume_at"] is not None
    assert ph["earliest_resume_at"][:13] == t_min.isoformat()[:13], (
        ph["earliest_resume_at"], t_min.isoformat()
    )


# ─── POOLV-02 ────────────────────────────────────────────────────────────────

async def test_attached_senders_enriched(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_running_campaign_factory,
):
    """POOLV-02: attached_senders[] entries carry restriction_status + restricted_until.

    A frozen sender reports restriction_status='spam_limited' with the matching
    restricted_until; active senders report 'none' and None.
    """
    sub = "pool-enrich"
    await _bind(async_db_session, test_workspace.id, sub)
    camp, senders = await test_running_campaign_factory(sender_count=2)
    headers = _auth_headers(valid_supabase_jwt, sub)

    frozen, active = senders[0], senders[1]
    until = (await async_db_session.execute(
        text("SELECT NOW() + INTERVAL '8 hours'")
    )).scalar_one()
    await _freeze_sender(async_db_session, frozen.id, "spam_limited", until=until)

    r = await async_client.get(f"/api/v1/campaigns/{camp['id']}", headers=headers)
    assert r.status_code == 200, r.text
    by_id = {str(s["id"]): s for s in r.json()["attached_senders"]}

    frozen_resp = by_id[str(frozen.id)]
    assert frozen_resp["restriction_status"] == "spam_limited", frozen_resp
    assert frozen_resp["restricted_until"] is not None, frozen_resp
    assert frozen_resp["restricted_until"][:13] == until.isoformat()[:13], (
        frozen_resp["restricted_until"], until.isoformat()
    )

    active_resp = by_id[str(active.id)]
    assert active_resp["restriction_status"] == "none", active_resp
    assert active_resp["restricted_until"] is None, active_resp
