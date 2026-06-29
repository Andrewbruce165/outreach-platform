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


# ── WARM-06 / WARM-10: settings (master toggle + content) ────────────────────


async def test_settings_get_resolves_defaults(async_client, valid_supabase_jwt, async_db_session):
    """GET /settings with no row → resolved code-defaults (24 topics, default prompt),
    enabled=false (explicit opt-in, D-06/D-10)."""
    from app.services.warmup import WARMUP_SYSTEM_PROMPT, WARMUP_TOPICS

    db = async_db_session
    wid = await _make_workspace(db)
    uid = f"user-{_uuid.uuid4()}"
    await _bind(db, wid, uid)

    resp = await async_client.get(
        "/api/v1/warmup/settings", headers=_auth_headers(valid_supabase_jwt, uid),
    )
    assert resp.status_code == 200, f"settings GET unavailable (got {resp.status_code})"
    body = resp.json()
    assert body["enabled"] is False, "no row → master toggle OFF (explicit opt-in, D-06)"
    assert body["topics"] == list(WARMUP_TOPICS), "empty topics → 24 default RU topics (D-10)"
    assert body["system_prompt"] == WARMUP_SYSTEM_PROMPT, "NULL prompt → default prompt (D-10)"


async def test_settings_put_persists_master_toggle(async_client, valid_supabase_jwt, async_db_session):
    """PUT /settings enabled=true persists; GET reflects it (idempotent upsert, D-06)."""
    db = async_db_session
    wid = await _make_workspace(db)
    uid = f"user-{_uuid.uuid4()}"
    await _bind(db, wid, uid)
    headers = _auth_headers(valid_supabase_jwt, uid)

    put = await async_client.put(
        "/api/v1/warmup/settings",
        headers=headers,
        json={"enabled": True, "topics": ["погода"], "language": "ru"},
    )
    assert put.status_code == 200, f"settings PUT failed (got {put.status_code})"
    assert put.json()["status"] == "saved"

    get = await async_client.get("/api/v1/warmup/settings", headers=headers)
    assert get.status_code == 200
    body = get.json()
    assert body["enabled"] is True, "PUT enabled=true must persist (master toggle, D-06)"
    assert body["topics"] == ["погода"], "configured topics must round-trip (D-10)"

    # Idempotent upsert: second PUT updates the same row (no duplicate-PK error).
    put2 = await async_client.put(
        "/api/v1/warmup/settings", headers=headers, json={"enabled": False},
    )
    assert put2.status_code == 200
    get2 = await async_client.get("/api/v1/warmup/settings", headers=headers)
    assert get2.json()["enabled"] is False, "second PUT must update existing row (ON CONFLICT)"
