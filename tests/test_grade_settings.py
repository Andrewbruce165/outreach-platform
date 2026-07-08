"""Phase 22 Plan 02 — /sender-grade-settings API (D-16 + cross-tenant guard V4).

Mirrors test_warmup_router.py: an in-process AuthDep-scoped GET/PUT over the
per-workspace grade ladder. auth_dep lazily auto-creates a workspace for a fresh
JWT sub, so distinct `sub` values map to distinct workspaces — the mechanism the
cross-tenant test exploits (a token can only ever touch its own workspace's row).

Covered behaviours:
- GET with no row → code-defaults (5,30),(9,30),(13,null). (D-16)
- PUT then GET round-trips custom values. (D-16)
- PUT with chats_per_day=20 → 200 + a green-corridor warning. (D-16)
- Workspace A's PUT does not leak into workspace B's GET. (V4 cross-tenant)
"""

import uuid as _uuid

import pytest

pytestmark = pytest.mark.asyncio

_URL = "/api/v1/sender-grade-settings"


def _auth_headers(jwt_factory, sub: str) -> dict:
    return {"Authorization": f"Bearer {jwt_factory(sub=sub)}"}


def _levels(payload: dict) -> list[tuple]:
    """Extract [(chats_per_day, step_days), ...] ordered by level from a response."""
    return [
        (lvl["chats_per_day"], lvl["step_days"])
        for lvl in sorted(payload["levels"], key=lambda x: x["level"])
    ]


async def test_get_defaults_when_no_row(async_client, valid_supabase_jwt):
    """A workspace with no row resolves to code-defaults 5/30, 9/30, 13 (D-16)."""
    sub = f"grade-defaults-{_uuid.uuid4()}"
    resp = await async_client.get(_URL, headers=_auth_headers(valid_supabase_jwt, sub))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert _levels(body) == [(5, 30), (9, 30), (13, None)]  # D-16 code-defaults
    # Level 3 is permanent — no step (D-17).
    assert body["levels"][2]["step_days"] is None


async def test_put_then_get_roundtrips_custom_values(async_client, valid_supabase_jwt):
    """PUT custom in-corridor values, GET reads them back (D-16)."""
    sub = f"grade-roundtrip-{_uuid.uuid4()}"
    headers = _auth_headers(valid_supabase_jwt, sub)

    put = await async_client.put(
        _URL,
        headers=headers,
        json={
            "level1_chats_per_day": 3,
            "level1_step_days": 45,
            "level2_chats_per_day": 7,
            "level2_step_days": 60,
            "level3_chats_per_day": 11,
        },
    )
    assert put.status_code == 200, put.text
    assert put.json()["status"] == "saved"
    assert put.json()["warnings"] == []  # all inside the green corridor

    got = await async_client.get(_URL, headers=headers)
    assert got.status_code == 200, got.text
    assert _levels(got.json()) == [(3, 45), (7, 60), (11, None)]


async def test_put_out_of_corridor_returns_warning_but_200(async_client, valid_supabase_jwt):
    """chats_per_day=20 (> soft cap 13, <= hard cap 100) → 200 + a warning (D-16)."""
    sub = f"grade-warn-{_uuid.uuid4()}"
    headers = _auth_headers(valid_supabase_jwt, sub)

    put = await async_client.put(
        _URL,
        headers=headers,
        json={
            "level1_chats_per_day": 20,   # exceeds green corridor (13)
            "level1_step_days": 30,
            "level2_chats_per_day": 9,
            "level2_step_days": 30,
            "level3_chats_per_day": 13,
        },
    )
    assert put.status_code == 200, put.text
    warnings = put.json()["warnings"]
    assert any(
        w["field"] == "level1_chats_per_day" and w["value"] == 20
        for w in warnings
    ), f"expected a green-corridor warning for level1_chats_per_day, got {warnings}"
    # The value is still persisted despite the soft breach.
    got = await async_client.get(_URL, headers=headers)
    assert _levels(got.json())[0] == (20, 30)


async def test_hard_cap_rejected(async_client, valid_supabase_jwt):
    """chats_per_day beyond the Pydantic hard bound (le=100) → 422 (T-22-05)."""
    sub = f"grade-hardcap-{_uuid.uuid4()}"
    headers = _auth_headers(valid_supabase_jwt, sub)
    put = await async_client.put(
        _URL,
        headers=headers,
        json={
            "level1_chats_per_day": 500,  # > hard cap 100
            "level1_step_days": 30,
            "level2_chats_per_day": 9,
            "level2_step_days": 30,
            "level3_chats_per_day": 13,
        },
    )
    assert put.status_code == 422, put.text


async def test_cross_tenant_isolation(async_client, valid_supabase_jwt):
    """Workspace A's ladder is invisible/unwritable to workspace B (V4).

    Each token auto-resolves to its OWN workspace (auth_dep lazy-create). A writes
    a distinctive ladder; B still reads code-defaults and A's write never appears
    in B's response — proving the upsert + read are scoped by ctx.workspace_id.
    """
    sub_a = f"tenant-a-{_uuid.uuid4()}"
    sub_b = f"tenant-b-{_uuid.uuid4()}"
    headers_a = _auth_headers(valid_supabase_jwt, sub_a)
    headers_b = _auth_headers(valid_supabase_jwt, sub_b)

    # A writes a distinctive ladder.
    put_a = await async_client.put(
        _URL,
        headers=headers_a,
        json={
            "level1_chats_per_day": 2,
            "level1_step_days": 99,
            "level2_chats_per_day": 4,
            "level2_step_days": 88,
            "level3_chats_per_day": 6,
        },
    )
    assert put_a.status_code == 200, put_a.text

    # B still sees code-defaults — A's write did not leak across the tenant boundary.
    got_b = await async_client.get(_URL, headers=headers_b)
    assert got_b.status_code == 200, got_b.text
    assert _levels(got_b.json()) == [(5, 30), (9, 30), (13, None)], (
        "workspace B must NOT see workspace A's ladder — settings not scoped by "
        "ctx.workspace_id (V4)"
    )

    # And A still sees its own values (sanity: the write did land somewhere).
    got_a = await async_client.get(_URL, headers=headers_a)
    assert _levels(got_a.json()) == [(2, 99), (4, 88), (6, None)]
