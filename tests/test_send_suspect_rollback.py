"""Quick 260706-e8s (T2) — send-path suspect-resolve rollback tests.

Mirrors the Phase 14/17 CHECKER-side "suspect / rollback" semantics onto the
message-SEND path. The 2026-07-06 07:31 incident: sender-7867638054 returned
"+79185782285 не зарегистрирован в Telegram" (a resolve false-negative) and 12s
later caught PEER_FLOOD; the number was alive (a healthy account reached it at
08:19). On the send path this false-negative was neither rerouted nor was the
poisoned resolve cache purged.

Two complementary, bounded behaviours are covered:
  1. Reactive rollback (`app.services.send_suspect.rollback_suspect_resolve_fails`)
     — when a sender is flagged spam_limited/frozen, its NOT_REGISTERED / PRIVACY
     refusals in the last SUSPECT_RESOLVE_WINDOW_MINUTES are rerouted onto a
     healthy UNTRIED pool sender and the false is_registered=false cache rows are
     purged.  (Tests A–F)
  2. Preventive re-rotation + marker stamping in `app.services.queue` — a
     NOT_REGISTERED on the send path re-rotates onto an untried healthy sender and
     finalizes (with a resolve-fail marker) only when the pool is exhausted; the
     PEER_FLOOD / ACCOUNT_FROZEN branches invoke the reactive rollback.  (Tests G–J)

The module-under-test import is done INSIDE each test body (mirrors
tests/test_failover.py) so `pytest --collect-only` stays clean while the tests
still ERROR/FAIL RED before the implementation lands.

Test → requirement map:
- test_reactive_reroute_to_healthy_untried   → A (reactive reroute)
- test_reactive_cache_purge                  → B (poisoned cache purge, windowed)
- test_reactive_no_receiver_best_effort      → C (no healthy receiver — leave failed)
- test_reactive_window_guard                 → D (outside 15-min window — not moved)
- test_reactive_non_running_guard            → E (paused/done campaign — not resurrected)
- test_reactive_tried_exclusion_bounded      → F (only UNTRIED healthy senders)
- test_peer_flood_invokes_rollback           → G (PEER_FLOOD wiring)
- test_account_frozen_invokes_rollback       → G (ACCOUNT_FROZEN wiring)
- test_not_registered_preventive_reroute     → H (send-path NOT_REGISTERED reroute)
- test_not_registered_finalizes_when_exhausted → I (finalize + markers, bounded)
- test_0731_incident_resolves_automatically  → J (07:31 incident end-to-end)
"""

import json
import logging

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


# ─── Seed / assert helpers ────────────────────────────────────────────────────

async def _seed_failed_resolve_row(
    db, ws_id, campaign_id, sender_id, phone, *,
    code: str = "RECIPIENT_NOT_IN_TELEGRAM",
    finished_minutes_ago: int = 1,
    tried=None,
    with_cca: bool = True,
    attempts: int = 0,
):
    """Insert a `message_queue` row exactly as a finalized send-path resolve-fail
    would look: status='failed', finished_at within/without the window, the
    resolve-fail marker in extra_data, plus a sticky CCA pointing at the sender."""
    ed = {"resolve_fail_code": code, "resolve_fail_sender": str(sender_id)}
    if tried is not None:
        ed["nr_tried_senders"] = [str(t) for t in tried]
    await db.execute(text("""
        INSERT INTO message_queue (
            workspace_id, campaign_id, sender_id, recipient_phone, item_type,
            status, scheduled_at, finished_at, attempts, error_message, extra_data
        ) VALUES (
            :wid, :cid, :sid, :phone, 'message', 'failed',
            NOW() - make_interval(mins => :fin),
            NOW() - make_interval(mins => :fin),
            :attempts, 'RECIPIENT_NOT_IN_TELEGRAM (test)', CAST(:ed AS JSONB)
        )
    """), {
        "wid": str(ws_id), "cid": str(campaign_id), "sid": str(sender_id),
        "phone": phone, "fin": finished_minutes_ago, "attempts": attempts,
        "ed": json.dumps(ed),
    })
    if with_cca:
        await db.execute(text("""
            INSERT INTO campaign_contact_assignments
                (workspace_id, campaign_id, contact_phone, sender_id)
            VALUES (:wid, :cid, :phone, :sid)
            ON CONFLICT (campaign_id, contact_phone)
                DO UPDATE SET sender_id = EXCLUDED.sender_id
        """), {"wid": str(ws_id), "cid": str(campaign_id), "phone": phone, "sid": str(sender_id)})
    await db.commit()


async def _flag_sender(db, sender_id, status: str = "spam_limited"):
    """Flag a sender restricted exactly as the queue.py freeze paths do — the flag
    MUST land before rollback runs so restriction_status='none' excludes it."""
    await db.execute(text("""
        UPDATE senders
        SET restriction_status = :st, restricted_until = NOW() + INTERVAL '24 hours'
        WHERE id = :sid
    """), {"st": status, "sid": str(sender_id)})
    await db.commit()


async def _seed_cache(db, ws_id, sender_id, phone, is_registered: bool, updated_minutes_ago: int = 0):
    await db.execute(text("""
        INSERT INTO contacts_cache (workspace_id, sender_id, phone, is_registered, updated_at)
        VALUES (:wid, :sid, :phone, :reg, NOW() - make_interval(mins => :ago))
    """), {
        "wid": str(ws_id), "sid": str(sender_id), "phone": phone,
        "reg": is_registered, "ago": updated_minutes_ago,
    })
    await db.commit()


async def _row(db, campaign_id, phone):
    return (await db.execute(text("""
        SELECT id, sender_id, status, attempts, error_message, finished_at,
               scheduled_at, extra_data
        FROM message_queue
        WHERE campaign_id = :cid AND recipient_phone = :phone
        ORDER BY created_at DESC LIMIT 1
    """), {"cid": str(campaign_id), "phone": phone})).first()


async def _cca_sender_for(db, campaign_id, phone):
    row = (await db.execute(text("""
        SELECT sender_id FROM campaign_contact_assignments
        WHERE campaign_id = :cid AND contact_phone = :phone
    """), {"cid": str(campaign_id), "phone": phone})).first()
    return str(row[0]) if row else None


async def _cache_exists(db, sender_id, phone) -> bool:
    row = (await db.execute(text("""
        SELECT 1 FROM contacts_cache WHERE sender_id = :sid AND phone = :phone
    """), {"sid": str(sender_id), "phone": phone})).first()
    return row is not None


async def _now(db):
    return (await db.execute(text("SELECT NOW()"))).scalar()


# ─── Test A — reactive reroute to a healthy untried sender ────────────────────

async def test_reactive_reroute_to_healthy_untried(
    async_db_session, test_running_campaign_factory,
):
    from app.services.send_suspect import rollback_suspect_resolve_fails

    camp, senders = await test_running_campaign_factory(sender_count=2)
    a, b = senders[0], senders[1]
    phone = "+79185782285"

    await _seed_failed_resolve_row(
        async_db_session, camp["workspace_id"], camp["id"], a.id, phone,
        finished_minutes_ago=1, with_cca=True,
    )
    await _flag_sender(async_db_session, a.id, "spam_limited")

    now_before = await _now(async_db_session)
    moved = await rollback_suspect_resolve_fails(a.id, async_db_session)

    assert moved == 1, "the suspect resolve-fail row must reroute to the healthy sender"
    row = await _row(async_db_session, camp["id"], phone)
    assert str(row.sender_id) == str(b.id), "row must move to the healthy sender B"
    assert row.status == "pending", "row must be re-queued pending"
    assert row.attempts == 0, "attempts reset on reroute"
    assert row.error_message is None, "error_message cleared on reroute"
    assert row.finished_at is None, "finished_at cleared on reroute"
    assert row.scheduled_at is not None and row.scheduled_at >= now_before, (
        "scheduled_at reset to ~NOW() so B can send immediately"
    )
    assert await _cca_sender_for(async_db_session, camp["id"], phone) == str(b.id), (
        "sticky CCA must repoint at the healthy sender"
    )


# ─── Test B — poisoned cache purge (windowed, is_registered=false only) ───────

async def test_reactive_cache_purge(
    async_db_session, test_running_campaign_factory,
):
    from app.services.send_suspect import rollback_suspect_resolve_fails

    camp, senders = await test_running_campaign_factory(sender_count=2)
    a = senders[0]

    false_in_window = "+79000000001"      # is_registered=false, fresh → DELETED
    true_in_window = "+79000000002"       # is_registered=true, fresh   → LEFT
    false_out_of_window = "+79000000003"  # is_registered=false, old    → LEFT

    await _seed_cache(async_db_session, camp["workspace_id"], a.id, false_in_window, False, 1)
    await _seed_cache(async_db_session, camp["workspace_id"], a.id, true_in_window, True, 1)
    await _seed_cache(async_db_session, camp["workspace_id"], a.id, false_out_of_window, False, 30)

    await _flag_sender(async_db_session, a.id, "spam_limited")

    await rollback_suspect_resolve_fails(a.id, async_db_session)

    assert not await _cache_exists(async_db_session, a.id, false_in_window), (
        "fresh is_registered=false cache row (poisoned) must be purged"
    )
    assert await _cache_exists(async_db_session, a.id, true_in_window), (
        "is_registered=true cache row must be left intact"
    )
    assert await _cache_exists(async_db_session, a.id, false_out_of_window), (
        "is_registered=false row outside the window must be left intact"
    )


# ─── Test C — no healthy receiver → best-effort, leave failed ─────────────────

async def test_reactive_no_receiver_best_effort(
    async_db_session, test_running_campaign_factory,
):
    from app.services.send_suspect import rollback_suspect_resolve_fails

    camp, senders = await test_running_campaign_factory(sender_count=1)
    a = senders[0]
    phone = "+79185700001"

    await _seed_failed_resolve_row(
        async_db_session, camp["workspace_id"], camp["id"], a.id, phone,
        finished_minutes_ago=1, with_cca=True,
    )
    await _flag_sender(async_db_session, a.id, "spam_limited")

    moved = await rollback_suspect_resolve_fails(a.id, async_db_session)

    assert moved == 0, "no healthy receiver → nothing moved (best-effort)"
    row = await _row(async_db_session, camp["id"], phone)
    assert row.status == "failed", "row stays failed on the flagged sender"
    assert str(row.sender_id) == str(a.id)


# ─── Test D — window guard (outside 15-min window → not moved) ────────────────

async def test_reactive_window_guard(
    async_db_session, test_running_campaign_factory,
):
    from app.services.send_suspect import rollback_suspect_resolve_fails

    camp, senders = await test_running_campaign_factory(sender_count=2)
    a = senders[0]
    phone = "+79185700002"

    await _seed_failed_resolve_row(
        async_db_session, camp["workspace_id"], camp["id"], a.id, phone,
        finished_minutes_ago=30, with_cca=True,  # 30 min ago → outside 15-min window
    )
    await _flag_sender(async_db_session, a.id, "spam_limited")

    moved = await rollback_suspect_resolve_fails(a.id, async_db_session)

    assert moved == 0, "resolve-fail older than the window must NOT be clawed back"
    row = await _row(async_db_session, camp["id"], phone)
    assert row.status == "failed"
    assert str(row.sender_id) == str(a.id)


# ─── Test E — non-running campaign guard (never resurrect zombies) ────────────

async def test_reactive_non_running_guard(
    async_db_session, test_campaign_factory, test_sender_factory,
    attach_sender_to_campaign,
):
    from app.services.send_suspect import rollback_suspect_resolve_fails

    camp = await test_campaign_factory(status="paused")
    a = await test_sender_factory()
    b = await test_sender_factory()
    await attach_sender_to_campaign(camp["id"], a.id)
    await attach_sender_to_campaign(camp["id"], b.id)
    phone = "+79185700003"

    await _seed_failed_resolve_row(
        async_db_session, camp["workspace_id"], camp["id"], a.id, phone,
        finished_minutes_ago=1, with_cca=True,
    )
    await _flag_sender(async_db_session, a.id, "spam_limited")

    moved = await rollback_suspect_resolve_fails(a.id, async_db_session)

    assert moved == 0, "a paused campaign's failed row must NOT be resurrected (WR-17)"
    row = await _row(async_db_session, camp["id"], phone)
    assert row.status == "failed"
    assert str(row.sender_id) == str(a.id)


# ─── Test F — tried-exclusion / bounded (only UNTRIED healthy senders) ────────

async def test_reactive_tried_exclusion_bounded(
    async_db_session, test_running_campaign_factory,
):
    from app.services.send_suspect import rollback_suspect_resolve_fails

    camp, senders = await test_running_campaign_factory(sender_count=2)
    a, b = senders[0], senders[1]
    phone = "+79185700004"

    # nr_tried_senders already lists the only other healthy sender B → nothing untried.
    await _seed_failed_resolve_row(
        async_db_session, camp["workspace_id"], camp["id"], a.id, phone,
        finished_minutes_ago=1, with_cca=True, tried=[b.id],
    )
    await _flag_sender(async_db_session, a.id, "spam_limited")

    moved = await rollback_suspect_resolve_fails(a.id, async_db_session)

    assert moved == 0, "no UNTRIED healthy sender → leave the row failed (bounded)"
    row = await _row(async_db_session, camp["id"], phone)
    assert row.status == "failed"
    assert str(row.sender_id) == str(a.id)
