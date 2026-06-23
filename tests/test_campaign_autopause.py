"""029: auto-pause visibility.

CampaignEnqueueWorker flips a running campaign to ``paused`` with a
``pause_reason`` when it can no longer send — hard blocker only: ZERO eligible
senders (pool empty or all restricted/offline/auth-failed) AND outstanding work.
A campaign with nothing left to do is left running (effectively finished).
Resume/start clear the reason.
"""

import pytest
from sqlalchemy import text

from app.services.campaign_enqueue import CampaignEnqueueWorker

pytestmark = pytest.mark.asyncio


async def _restrict(db, sender_id, status="spam_limited"):
    await db.execute(
        text("UPDATE senders SET restriction_status = :s WHERE id = :id"),
        {"s": status, "id": str(sender_id)},
    )


async def _campaign_state(db, cid):
    return (await db.execute(
        text("SELECT status, pause_reason, paused_at FROM campaigns WHERE id = :id"),
        {"id": str(cid)},
    )).fetchone()


async def _move_into_folder(db, contact, folder_id):
    await db.execute(
        text("UPDATE contacts SET folder_id = :fid WHERE id = :cid"),
        {"fid": str(folder_id), "cid": str(contact.id)},
    )


async def test_autopause_when_all_senders_restricted_and_work_remains(
    async_db_session, test_running_campaign_factory, test_contacts_factory,
):
    """Sole sender restricted + a registered contact waiting → auto-paused with
    reason 'senders_unavailable'."""
    camp, senders = await test_running_campaign_factory(sender_count=1)
    contact = await test_contacts_factory(count=1, tg_status="registered")
    await _move_into_folder(async_db_session, contact, camp["folder_id"])
    await _restrict(async_db_session, senders[0].id, "spam_limited")
    await async_db_session.commit()

    await (CampaignEnqueueWorker())._tick()

    state = await _campaign_state(async_db_session, camp["id"])
    assert state.status == "paused"
    assert state.pause_reason == "senders_unavailable"
    assert state.paused_at is not None


async def test_no_autopause_when_eligible_sender_exists(
    async_db_session, test_running_campaign_factory, test_contacts_factory,
):
    """A healthy sender in the pool → campaign keeps running (and enqueues)."""
    camp, _senders = await test_running_campaign_factory(sender_count=1)
    contact = await test_contacts_factory(count=1, tg_status="registered")
    await _move_into_folder(async_db_session, contact, camp["folder_id"])
    await async_db_session.commit()

    await (CampaignEnqueueWorker())._tick()

    state = await _campaign_state(async_db_session, camp["id"])
    assert state.status == "running"
    assert state.pause_reason is None


async def test_no_autopause_when_no_work_remains(
    async_db_session, test_running_campaign_factory,
):
    """No eligible sender but nothing to send (empty folder, no pending) →
    campaign is effectively finished, not blocked → left running."""
    camp, senders = await test_running_campaign_factory(sender_count=1)
    await _restrict(async_db_session, senders[0].id, "frozen")
    await async_db_session.commit()

    await (CampaignEnqueueWorker())._tick()

    state = await _campaign_state(async_db_session, camp["id"])
    assert state.status == "running"
    assert state.pause_reason is None


async def test_resume_clears_autopause_reason(
    async_client, valid_supabase_jwt, async_db_session, test_workspace,
    test_running_campaign_factory,
):
    """POST /resume clears pause_reason / paused_at left by an auto-pause."""
    camp, _senders = await test_running_campaign_factory(sender_count=1)
    # Simulate an auto-pause that the worker would have written.
    await async_db_session.execute(
        text("""
            UPDATE campaigns
            SET status = 'paused', pause_reason = 'senders_unavailable', paused_at = NOW()
            WHERE id = :id
        """),
        {"id": str(camp["id"])},
    )
    await async_db_session.execute(
        text("""
            INSERT INTO user_workspaces (supabase_user_id, workspace_id, role)
            VALUES (:uid, :wid, 'owner') ON CONFLICT DO NOTHING
        """),
        {"uid": "u-autopause", "wid": str(test_workspace.id)},
    )
    await async_db_session.commit()

    r = await async_client.post(
        f"/api/v1/campaigns/{camp['id']}/resume",
        headers={"Authorization": f"Bearer {valid_supabase_jwt(sub='u-autopause')}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "running"
    assert body["pause_reason"] is None
    assert body["paused_at"] is None
