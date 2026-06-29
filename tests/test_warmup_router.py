"""Phase 15 — Warmup router RED guards (WARM-05).

These tests are intentionally RED at the end of Plan 15-01. They assert the
workspace-scoped rewrite of ``/api/v1/warmup`` (D-05) that Plan 04 implements:

- test_pool_workspace_scoped: a sender in workspace A must be invisible to a
  ``GET /api/v1/warmup/pool`` authenticated as workspace B (and vice-versa).
  The legacy router is on ``verify_api_key`` (global, no workspace scope) and is
  not even mounted under ``AuthDep`` yet — so this is RED.
- test_response_shapes_preserved: ``GET /pool`` must return objects whose keys
  are a SUPERSET of the canonical set AND include the new D-11 keys
  ``restriction_status`` / ``restricted_until``; ``is_active`` must NOT be a
  required key (it was dropped in migration 013). RED until the rewrite.

RED rationale: the workspace-scoped, AuthDep-mounted ``/api/v1/warmup`` endpoint
does not exist yet (the legacy router imports a missing ``app.routers.auth`` and
is unmounted), so authenticated requests do not return the scoped 200 + shape
these tests demand. The assertions fail for the right reason (behaviour missing),
not via import/syntax errors.
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


async def _make_workspace(db) -> str:
    wid = str(_uuid.uuid4())
    await db.execute(text("INSERT INTO workspaces (id, name) VALUES (:id, :n)"),
                     {"id": wid, "n": f"WS {wid[:8]}"})
    await db.commit()
    return wid


async def _make_sender(db, wid: str, slug: str) -> str:
    sid = str(_uuid.uuid4())
    await db.execute(text("""
        INSERT INTO senders (id, workspace_id, slug, name, phone, session_string,
                             role, auth_status, lifecycle_status,
                             rate_per_min, rate_per_hour, rate_per_day)
        VALUES (:id, :wid, :slug, :name, :phone, 'stub',
                'sender', 'ok', 'active', 4, 20, 150)
    """), {"id": sid, "wid": wid, "slug": slug, "name": slug,
           "phone": f"+790{abs(hash(sid)) % 10_000_000:07d}"})
    await db.commit()
    return sid


# ── WARM-05: workspace scoping ───────────────────────────────────────────────


async def test_pool_workspace_scoped(async_client, valid_supabase_jwt, async_db_session):
    """A sender in workspace A is NOT visible to workspace B's /pool."""
    db = async_db_session
    wid_a = await _make_workspace(db)
    wid_b = await _make_workspace(db)
    uid_b = f"user-b-{_uuid.uuid4()}"
    await _bind(db, wid_b, uid_b)

    slug_a = f"a-only-{_uuid.uuid4().hex[:6]}"
    await _make_sender(db, wid_a, slug_a)

    resp = await async_client.get(
        "/api/v1/warmup/pool", headers=_auth_headers(valid_supabase_jwt, uid_b),
    )
    assert resp.status_code == 200, (
        f"workspace-scoped /pool not available yet (got {resp.status_code}) — "
        "router must be rewritten onto AuthDep (D-05)"
    )
    slugs = [s["slug"] for s in resp.json()["senders"]]
    assert slug_a not in slugs, (
        "workspace B must NOT see workspace A's sender — /pool not scoped by "
        "workspace_id (WARM-05)"
    )


async def test_response_shapes_preserved(async_client, valid_supabase_jwt, async_db_session):
    """/pool objects keep the canonical keys and ADD the D-11 restriction keys;
    `is_active` is NOT a required key (dropped in migration 013)."""
    db = async_db_session
    wid = await _make_workspace(db)
    uid = f"user-{_uuid.uuid4()}"
    await _bind(db, wid, uid)
    await _make_sender(db, wid, f"shape-{_uuid.uuid4().hex[:6]}")

    resp = await async_client.get(
        "/api/v1/warmup/pool", headers=_auth_headers(valid_supabase_jwt, uid),
    )
    assert resp.status_code == 200, (
        f"workspace-scoped /pool not available yet (got {resp.status_code}) — "
        "router must be rewritten onto AuthDep (D-05)"
    )
    senders = resp.json()["senders"]
    assert senders, "expected the workspace's sender to appear in /pool"
    keys = set(senders[0].keys())

    required = {
        "id", "slug", "name", "phone", "in_pool", "warmup_active",
        "enrolled_at", "enrolled_days", "level", "sent_today",
        # D-11 additions:
        "restriction_status", "restricted_until",
    }
    missing = required - keys
    assert not missing, f"/pool response missing required keys: {sorted(missing)} (WARM-05/D-11)"
    assert "is_active" not in required, (
        "is_active must NOT be a required key — column dropped in migration 013"
    )
