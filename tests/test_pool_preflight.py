"""Quick task 260706-fcq — pre-flight health check + checker/sender role separation.

Covers PFH-01 (recent-restriction advisory), PFH-02 (checker force-guard on attach),
PFH-03 (reverse-direction role->checker force-guard on PATCH /senders).

Test → requirement map:
- test_attach_clean_sender_no_warnings                       → PFH-01 (clean → [])
- test_attach_recent_restriction_warns                       → PFH-01 (in-window → RECENT_RESTRICTION, still attaches)
- test_attach_old_restriction_no_warn                        → PFH-01 (>7d → [])
- test_attach_cleared_event_no_warn                          → PFH-01 ('cleared' excluded)
- test_attach_checker_without_force_409                      → PFH-02 (checker no force → 409, no row)
- test_attach_checker_with_force_ok                          → PFH-02 (checker force=true → 200 + CHECKER_FORCE_ATTACHED)
- test_flip_to_checker_in_running_campaign_without_force_409 → PFH-03 (role flip in running → 409, role unchanged)
- test_flip_to_checker_in_running_campaign_with_force_ok     → PFH-03 (force=true → 200, role flips)
- test_flip_to_checker_not_in_running_campaign_ok            → PFH-03 (idle sender → 200, no force)

Isolation note (same as test_pool_endpoints.py): user_workspaces has a DB-level
UNIQUE(supabase_user_id), so each test uses a DISTINCT JWT sub bound to its own
per-test workspace.
"""

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


def _auth_headers(jwt_factory, sub: str) -> dict:
    return {"Authorization": f"Bearer {jwt_factory(sub=sub)}"}


async def _bind(db, ws_id, uid):
    """Bind a Supabase user (JWT sub) to a workspace as owner."""
    await db.execute(text("""
        INSERT INTO user_workspaces (supabase_user_id, workspace_id, role)
        VALUES (:uid, :wid, 'owner') ON CONFLICT DO NOTHING
    """), {"uid": uid, "wid": str(ws_id)})
    await db.commit()


async def _count_campaign_senders(db, campaign_id, sender_id) -> int:
    row = (await db.execute(text("""
        SELECT COUNT(*) FROM campaign_senders
        WHERE campaign_id = :cid AND sender_id = :sid
    """), {"cid": str(campaign_id), "sid": str(sender_id)})).scalar_one()
    return int(row)


async def _db_role(db, sender_id) -> str:
    return (await db.execute(
        text("SELECT role FROM senders WHERE id = :sid"),
        {"sid": str(sender_id)},
    )).scalar_one()


async def _insert_restriction_event(
    db, ws_id, sender_id, event_type,
    category="restriction", source="queue_error", age_days=0,
):
    """Seed one sender_restriction_events row `age_days` in the past."""
    await db.execute(text("""
        INSERT INTO sender_restriction_events
            (workspace_id, sender_id, category, event_type, source, created_at)
        VALUES (:wid, :sid, :cat, :et, :src, now() - make_interval(days => :age))
    """), {
        "wid": str(ws_id), "sid": str(sender_id), "cat": category,
        "et": event_type, "src": source, "age": age_days,
    })
    await db.commit()


# ─── PFH-01: clean attach ─────────────────────────────────────────────────────

async def test_attach_clean_sender_no_warnings(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory, test_sender_factory,
):
    """A fresh role='sender' with no restriction history → 200, attach_warnings == []."""
    await _bind(async_db_session, test_workspace.id, "pf-clean")
    camp = await test_campaign_factory(status="draft")
    sender = await test_sender_factory()

    r = await async_client.post(
        f"/api/v1/campaigns/{camp['id']}/senders",
        json={"sender_id": str(sender.id)},
        headers=_auth_headers(valid_supabase_jwt, "pf-clean"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["attach_warnings"] == []
    assert await _count_campaign_senders(async_db_session, camp["id"], sender.id) == 1


# ─── PFH-01: recent restriction warns (still attaches) ────────────────────────

async def test_attach_recent_restriction_warns(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory, test_sender_factory,
):
    """An in-window non-'cleared' restriction event → 200 + RECENT_RESTRICTION warning,
    and the campaign_senders row IS created (warning, not block)."""
    await _bind(async_db_session, test_workspace.id, "pf-recent")
    camp = await test_campaign_factory(status="draft")
    sender = await test_sender_factory()
    await _insert_restriction_event(
        async_db_session, test_workspace.id, sender.id,
        event_type="spam_limited", age_days=0,
    )

    r = await async_client.post(
        f"/api/v1/campaigns/{camp['id']}/senders",
        json={"sender_id": str(sender.id)},
        headers=_auth_headers(valid_supabase_jwt, "pf-recent"),
    )
    assert r.status_code == 200, r.text
    warnings = r.json()["attach_warnings"]
    assert len(warnings) == 1
    w = warnings[0]
    assert w["code"] == "RECENT_RESTRICTION"
    assert w["event_type"] == "spam_limited"
    assert str(w["sender_id"]) == str(sender.id)
    # Warning, NOT a block — the row is created.
    assert await _count_campaign_senders(async_db_session, camp["id"], sender.id) == 1


# ─── PFH-01: old restriction (>7d) → no warning ───────────────────────────────

async def test_attach_old_restriction_no_warn(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory, test_sender_factory,
):
    """A restriction event older than 7 days is outside the window → no warning."""
    await _bind(async_db_session, test_workspace.id, "pf-old")
    camp = await test_campaign_factory(status="draft")
    sender = await test_sender_factory()
    await _insert_restriction_event(
        async_db_session, test_workspace.id, sender.id,
        event_type="spam_limited", age_days=10,
    )

    r = await async_client.post(
        f"/api/v1/campaigns/{camp['id']}/senders",
        json={"sender_id": str(sender.id)},
        headers=_auth_headers(valid_supabase_jwt, "pf-old"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["attach_warnings"] == []


# ─── PFH-01: 'cleared' recovery event excluded ────────────────────────────────

async def test_attach_cleared_event_no_warn(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory, test_sender_factory,
):
    """Only a 'cleared' (recovery) event in-window → no warning (excluded)."""
    await _bind(async_db_session, test_workspace.id, "pf-cleared")
    camp = await test_campaign_factory(status="draft")
    sender = await test_sender_factory()
    await _insert_restriction_event(
        async_db_session, test_workspace.id, sender.id,
        event_type="cleared", source="reconcile", age_days=0,
    )

    r = await async_client.post(
        f"/api/v1/campaigns/{camp['id']}/senders",
        json={"sender_id": str(sender.id)},
        headers=_auth_headers(valid_supabase_jwt, "pf-cleared"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["attach_warnings"] == []


# ─── PFH-02: checker without force → 409 ──────────────────────────────────────

async def test_attach_checker_without_force_409(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory, test_sender_factory,
):
    """Attaching a role='checker' account without force → 409 CHECKER_ROLE_CONFLICT,
    no campaign_senders row created."""
    await _bind(async_db_session, test_workspace.id, "pf-chk-noforce")
    camp = await test_campaign_factory(status="draft")
    checker = await test_sender_factory(role="checker", slug="pf-checker-nf")

    r = await async_client.post(
        f"/api/v1/campaigns/{camp['id']}/senders",
        json={"sender_id": str(checker.id)},
        headers=_auth_headers(valid_supabase_jwt, "pf-chk-noforce"),
    )
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    code = detail["code"] if isinstance(detail, dict) else detail
    assert code == "CHECKER_ROLE_CONFLICT"
    assert await _count_campaign_senders(async_db_session, camp["id"], checker.id) == 0


# ─── PFH-02: checker with force=true → 200 + CHECKER_FORCE_ATTACHED ────────────

async def test_attach_checker_with_force_ok(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory, test_sender_factory,
):
    """Attaching a role='checker' account with force=true → 200, row created,
    attach_warnings carries CHECKER_FORCE_ATTACHED."""
    await _bind(async_db_session, test_workspace.id, "pf-chk-force")
    camp = await test_campaign_factory(status="draft")
    checker = await test_sender_factory(role="checker", slug="pf-checker-f")

    r = await async_client.post(
        f"/api/v1/campaigns/{camp['id']}/senders",
        json={"sender_id": str(checker.id), "force": True},
        headers=_auth_headers(valid_supabase_jwt, "pf-chk-force"),
    )
    assert r.status_code == 200, r.text
    codes = [w["code"] for w in r.json()["attach_warnings"]]
    assert "CHECKER_FORCE_ATTACHED" in codes
    assert await _count_campaign_senders(async_db_session, camp["id"], checker.id) == 1


# ─── PFH-03: reverse-direction guard on PATCH /senders/{slug} ─────────────────

async def test_flip_to_checker_in_running_campaign_without_force_409(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory, test_sender_factory, attach_sender_to_campaign,
):
    """Flipping a role='sender' that is in a RUNNING campaign to 'checker' without
    force → 409 CHECKER_ROLE_CONFLICT; role in DB stays 'sender'."""
    await _bind(async_db_session, test_workspace.id, "pf-rev-noforce")
    sender = await test_sender_factory(slug="pf-rev-nf")
    running = await test_campaign_factory(status="running", name="RevRunNF")
    await attach_sender_to_campaign(running["id"], sender.id)

    r = await async_client.patch(
        f"/api/v1/senders/{sender.slug}",
        json={"role": "checker"},
        headers=_auth_headers(valid_supabase_jwt, "pf-rev-noforce"),
    )
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    code = detail["code"] if isinstance(detail, dict) else detail
    assert code == "CHECKER_ROLE_CONFLICT"
    assert await _db_role(async_db_session, sender.id) == "sender"


async def test_flip_to_checker_in_running_campaign_with_force_ok(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_campaign_factory, test_sender_factory, attach_sender_to_campaign,
):
    """Same flip with force=true → 200; role in DB is now 'checker'."""
    await _bind(async_db_session, test_workspace.id, "pf-rev-force")
    sender = await test_sender_factory(slug="pf-rev-f")
    running = await test_campaign_factory(status="running", name="RevRunF")
    await attach_sender_to_campaign(running["id"], sender.id)

    r = await async_client.patch(
        f"/api/v1/senders/{sender.slug}",
        json={"role": "checker", "force": True},
        headers=_auth_headers(valid_supabase_jwt, "pf-rev-force"),
    )
    assert r.status_code == 200, r.text
    assert await _db_role(async_db_session, sender.id) == "checker"


async def test_flip_to_checker_not_in_running_campaign_ok(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_sender_factory,
):
    """A sender NOT in any running campaign flips to 'checker' without force → 200."""
    await _bind(async_db_session, test_workspace.id, "pf-rev-idle")
    sender = await test_sender_factory(slug="pf-rev-idle")

    r = await async_client.patch(
        f"/api/v1/senders/{sender.slug}",
        json={"role": "checker"},
        headers=_auth_headers(valid_supabase_jwt, "pf-rev-idle"),
    )
    assert r.status_code == 200, r.text
    assert await _db_role(async_db_session, sender.id) == "checker"
