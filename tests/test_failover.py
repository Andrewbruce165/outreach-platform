"""Phase 9 — Cold-contact failover service tests (Wave 0 RED stubs).

These tests fully ASSERT the behaviour of `app.services.failover.failover_cold_backlog`
which Plan 09-02 will implement. The module does not exist yet, so the import is done
INSIDE each test body — that keeps `pytest --collect-only` clean (no top-level
ImportError collection error) while the tests still ERROR/FAIL RED at run time. This is
the expected Wave-0 state per 09-VALIDATION.md (pattern lifted from test_rebalance.py:51).

Helper under test (signature fixed here, implemented in 09-02 — see 09-RESEARCH.md):

    async def failover_cold_backlog(frozen_sender_id, db=None) -> int:
        # Move the frozen sender's cold-pending backlog onto healthy pool senders.
        # db is None  → helper opens+commits its OWN session (queue.py callers).
        # db passed   → transaction-neutral, caller commits (listener antispam path).
        # Returns total rows moved (0 if nothing movable or no healthy receiver — D-13).

Every test passes the test's own async_db_session so the move is visible in-session.

Test → requirement map (contract — names consumed by later verify commands):
- test_failover_spreads_to_healthy_pool      → FAIL-01 (even spread over healthy pool)
- test_failover_excludes_frozen_as_receiver  → FAIL-01 / D-09 / Pitfall 1
- test_failover_skips_engaged                → FAIL-03 (predicate: only cold-pending)
- test_failover_moves_empty_conversation     → FAIL-03 / D-05 (empty conv still cold)
- test_failover_cca_in_sync                  → FAIL-04 (queue + CCA lock-step + NOW())
- test_failover_leaves_engaged               → FAIL-05 (engaged stays on frozen sender)
- test_failover_idempotent                   → FAIL-06 (2nd call moves 0, no change)
- test_failover_no_receiver_keeps_paused     → FAIL-07 / D-13 (no receiver → stays paused)
- test_failover_logs_count_no_pii            → FAIL-08 (log COUNT + UUIDs, never phones)

NOTE: FAIL-02 (the three freeze call sites actually invoke failover) lives in 09-02
alongside the call-site edits; it is intentionally NOT scaffolded here.
"""

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


# ─── Helpers (copied verbatim from tests/test_rebalance.py:26-41) ─────────────

async def _pending_counts(db, campaign_id) -> dict[str, int]:
    """{sender_id_str: pending_count} for a campaign."""
    rows = (await db.execute(text("""
        SELECT sender_id, COUNT(*) FROM message_queue
        WHERE campaign_id = :cid AND status = 'pending'
        GROUP BY sender_id
    """), {"cid": str(campaign_id)})).all()
    return {str(r[0]): int(r[1]) for r in rows}


async def _cca_sender_for(db, campaign_id, contact_phone):
    row = (await db.execute(text("""
        SELECT sender_id FROM campaign_contact_assignments
        WHERE campaign_id = :cid AND contact_phone = :phone
    """), {"cid": str(campaign_id), "phone": contact_phone})).first()
    return str(row[0]) if row else None


# ─── Local freeze-state helpers (mirror the real freeze paths) ────────────────

async def _freeze_sender(db, sender_id, status: str = "spam_limited"):
    """Flag a sender restricted exactly as the freeze paths do (queue.py / listener.py).

    The freeze write MUST land before failover runs (Pitfall 3) so the candidate
    filter (restriction_status='none') excludes this sender as a receiver.
    """
    await db.execute(text("""
        UPDATE senders
        SET restriction_status = :st,
            restricted_until = NOW() + INTERVAL '24 hours'
        WHERE id = :sid
    """), {"st": status, "sid": str(sender_id)})
    await db.commit()


async def _pause_pending(db, sender_id):
    """Push the sender's pending queue +24h — what every freeze path does before
    failover (queue.py:745 / listener.py:926). Moved rows must be reset to NOW()."""
    await db.execute(text("""
        UPDATE message_queue
        SET scheduled_at = NOW() + INTERVAL '24 hours'
        WHERE sender_id = :sid AND status = 'pending'
    """), {"sid": str(sender_id)})
    await db.commit()


async def _scheduled_at(db, campaign_id, phone, status="pending"):
    row = (await db.execute(text("""
        SELECT scheduled_at FROM message_queue
        WHERE campaign_id = :cid AND recipient_phone = :phone AND status = :st
    """), {"cid": str(campaign_id), "phone": phone, "st": status})).first()
    return row[0] if row else None


async def _sender_of(db, campaign_id, phone, status="pending"):
    row = (await db.execute(text("""
        SELECT sender_id FROM message_queue
        WHERE campaign_id = :cid AND recipient_phone = :phone AND status = :st
    """), {"cid": str(campaign_id), "phone": phone, "st": status})).first()
    return str(row[0]) if row else None


# ─── FAIL-01 ──────────────────────────────────────────────────────────────────

async def test_failover_spreads_to_healthy_pool(
    async_db_session, test_running_campaign_factory, test_queue_item_factory,
):
    """FAIL-01: the frozen sender's cold-pending backlog is reassigned to the healthy
    pool senders (even spread), returning the count moved."""
    from app.services.failover import failover_cold_backlog

    camp, senders = await test_running_campaign_factory(sender_count=3)
    frozen, h1, h2 = senders[0], senders[1], senders[2]

    # 4 cold-pending rows on the frozen sender; the two healthy senders hold none.
    phones = [f"+7990090{i:04d}" for i in range(4)]
    for ph in phones:
        await test_queue_item_factory(camp["id"], frozen.id, ph, status="pending",
                                      with_cca=True, with_conversation=False)

    await _freeze_sender(async_db_session, frozen.id)
    await _pause_pending(async_db_session, frozen.id)

    moved = await failover_cold_backlog(frozen.id, async_db_session)

    after = await _pending_counts(async_db_session, camp["id"])
    assert sum(after.values()) == 4, "failover must not create or drop rows"
    assert moved == 4, "all 4 cold-pending rows must move off the frozen sender"
    assert after.get(str(frozen.id), 0) == 0, "frozen sender holds no movable rows after"

    # Spread across BOTH healthy senders (more than one distinct receiver when N>=2).
    receivers = {sid for sid, cnt in after.items() if cnt > 0}
    assert receivers <= {str(h1.id), str(h2.id)}, "receivers must be healthy pool only"
    assert len(receivers) >= 2, "backlog must spread across >1 healthy receiver"


# ─── FAIL-01 / D-09 / Pitfall 1 ───────────────────────────────────────────────

async def test_failover_excludes_frozen_as_receiver(
    async_db_session, test_running_campaign_factory, test_queue_item_factory,
):
    """FAIL-01 / Pitfall 1: the frozen sender's stale CCA rows point at itself; failover
    must NOT hand the backlog back to the frozen sender (rotation short-circuit landmine)."""
    from app.services.failover import failover_cold_backlog

    camp, senders = await test_running_campaign_factory(sender_count=2)
    frozen, healthy = senders[0], senders[1]

    phones = [f"+7990091{i:04d}" for i in range(3)]
    for ph in phones:
        # with_cca=True → CCA.sender_id points at the (soon-to-be) frozen sender.
        await test_queue_item_factory(camp["id"], frozen.id, ph, status="pending",
                                      with_cca=True, with_conversation=False)

    await _freeze_sender(async_db_session, frozen.id)
    await _pause_pending(async_db_session, frozen.id)

    moved = await failover_cold_backlog(frozen.id, async_db_session)

    after = await _pending_counts(async_db_session, camp["id"])
    assert moved == 3
    assert after.get(str(frozen.id), 0) == 0, "frozen sender must hold 0 movable rows after"
    # No moved row's NEW sender is the frozen sender; CCA must repoint at the healthy one.
    for ph in phones:
        assert await _sender_of(async_db_session, camp["id"], ph) == str(healthy.id), (
            "moved queue row must not stay on / return to the frozen sender"
        )
        assert await _cca_sender_for(async_db_session, camp["id"], ph) == str(healthy.id)


# ─── FAIL-03 ──────────────────────────────────────────────────────────────────

async def test_failover_skips_engaged(
    async_db_session, test_running_campaign_factory, test_queue_item_factory,
):
    """FAIL-03: only cold-pending rows move. Rows whose contact already has a
    sent/processing queue row OR an engaged (has-message) conversation stay on frozen."""
    from app.services.failover import failover_cold_backlog

    camp, senders = await test_running_campaign_factory(sender_count=2)
    frozen, healthy = senders[0], senders[1]

    sent_phone = "+79900920001"        # already sent in this campaign → not movable
    processing_phone = "+79900920002"  # mid-send → not movable
    engaged_phone = "+79900920003"     # pending BUT has a has-message conversation
    cold_phone = "+79900920004"        # the only truly-movable row

    await test_queue_item_factory(camp["id"], frozen.id, sent_phone, status="sent",
                                  with_cca=True, with_conversation=False)
    await test_queue_item_factory(camp["id"], frozen.id, processing_phone,
                                  status="processing", with_cca=True,
                                  with_conversation=False)
    await test_queue_item_factory(camp["id"], frozen.id, engaged_phone, status="pending",
                                  with_cca=True, with_conversation=True, with_message=True)
    await test_queue_item_factory(camp["id"], frozen.id, cold_phone, status="pending",
                                  with_cca=True, with_conversation=False)

    await _freeze_sender(async_db_session, frozen.id)
    await _pause_pending(async_db_session, frozen.id)

    moved = await failover_cold_backlog(frozen.id, async_db_session)

    assert moved == 1, "only the single cold-pending row is movable"
    # sent / processing / engaged rows all remain with the frozen sender.
    assert await _sender_of(async_db_session, camp["id"], sent_phone, "sent") == str(frozen.id)
    assert await _sender_of(async_db_session, camp["id"], processing_phone, "processing") == str(frozen.id)
    assert await _sender_of(async_db_session, camp["id"], engaged_phone, "pending") == str(frozen.id)
    # the cold one moved off the frozen sender to the healthy one.
    assert await _sender_of(async_db_session, camp["id"], cold_phone, "pending") == str(healthy.id)


# ─── FAIL-03 / D-05 ───────────────────────────────────────────────────────────

async def test_failover_moves_empty_conversation(
    async_db_session, test_running_campaign_factory, test_queue_item_factory,
):
    """FAIL-03 / D-05: a cold-pending row whose contact has a conversation with ZERO
    messages is STILL cold and MUST be moved (empty conversation != engaged)."""
    from app.services.failover import failover_cold_backlog

    camp, senders = await test_running_campaign_factory(sender_count=2)
    frozen, healthy = senders[0], senders[1]

    empty_conv_phone = "+79900930001"  # conversation row, but NO messages → still cold
    await test_queue_item_factory(camp["id"], frozen.id, empty_conv_phone,
                                  status="pending", with_cca=True,
                                  with_conversation=True, with_message=False)

    await _freeze_sender(async_db_session, frozen.id)
    await _pause_pending(async_db_session, frozen.id)

    moved = await failover_cold_backlog(frozen.id, async_db_session)

    assert moved == 1, "empty-conversation row is cold and must be moved (D-05)"
    assert await _sender_of(async_db_session, camp["id"], empty_conv_phone) == str(healthy.id)
    assert await _cca_sender_for(async_db_session, camp["id"], empty_conv_phone) == str(healthy.id)


# ─── FAIL-04 ──────────────────────────────────────────────────────────────────

async def test_failover_cca_in_sync(
    async_db_session, test_running_campaign_factory, test_queue_item_factory,
):
    """FAIL-04: every moved row updates message_queue.sender_id AND
    campaign_contact_assignments.sender_id in lock-step, and resets scheduled_at to
    NOW() (NOT the +24h freeze value)."""
    from app.services.failover import failover_cold_backlog

    camp, senders = await test_running_campaign_factory(sender_count=2)
    frozen, healthy = senders[0], senders[1]

    phones = [f"+7990094{i:04d}" for i in range(3)]
    for ph in phones:
        await test_queue_item_factory(camp["id"], frozen.id, ph, status="pending",
                                      with_cca=True, with_conversation=False)

    await _freeze_sender(async_db_session, frozen.id)
    await _pause_pending(async_db_session, frozen.id)

    moved = await failover_cold_backlog(frozen.id, async_db_session)
    assert moved == 3

    for ph in phones:
        q_sender = await _sender_of(async_db_session, camp["id"], ph)
        cca_sender = await _cca_sender_for(async_db_session, camp["id"], ph)
        assert q_sender == str(healthy.id), "queue row moved to the healthy sender"
        assert cca_sender == q_sender, "CCA.sender_id must match the moved queue row's sender"
        # scheduled_at must be reset to NOW() (sendable immediately) — not the +24h pause.
        sched = await _scheduled_at(async_db_session, camp["id"], ph)
        now_row = (await async_db_session.execute(text("SELECT NOW()"))).scalar()
        assert sched is not None and sched <= now_row, (
            "moved row scheduled_at must be <= NOW(), not the +24h freeze value"
        )


# ─── FAIL-05 ──────────────────────────────────────────────────────────────────

async def test_failover_leaves_engaged(
    async_db_session, test_running_campaign_factory, test_queue_item_factory,
):
    """FAIL-05: a contact who already exchanged a message (has-message conversation)
    keeps its pending row on the frozen sender — failover never breaks an engaged dialog."""
    from app.services.failover import failover_cold_backlog

    camp, senders = await test_running_campaign_factory(sender_count=2)
    frozen, healthy = senders[0], senders[1]

    engaged_phone = "+79900950001"
    await test_queue_item_factory(camp["id"], frozen.id, engaged_phone, status="pending",
                                  with_cca=True, with_conversation=True, with_message=True)

    await _freeze_sender(async_db_session, frozen.id)
    await _pause_pending(async_db_session, frozen.id)

    moved = await failover_cold_backlog(frozen.id, async_db_session)

    assert moved == 0, "engaged dialog row is not movable"
    assert await _sender_of(async_db_session, camp["id"], engaged_phone) == str(frozen.id), (
        "engaged row must stay on the frozen sender"
    )
    assert await _cca_sender_for(async_db_session, camp["id"], engaged_phone) == str(frozen.id)


# ─── FAIL-06 ──────────────────────────────────────────────────────────────────

async def test_failover_idempotent(
    async_db_session, test_running_campaign_factory, test_queue_item_factory,
):
    """FAIL-06: a second failover call moves 0 rows and leaves the distribution
    unchanged (idempotent / concurrency-safe)."""
    from app.services.failover import failover_cold_backlog

    camp, senders = await test_running_campaign_factory(sender_count=2)
    frozen, healthy = senders[0], senders[1]

    for i in range(3):
        await test_queue_item_factory(camp["id"], frozen.id, f"+7990096{i:04d}",
                                      status="pending", with_cca=True,
                                      with_conversation=False)

    await _freeze_sender(async_db_session, frozen.id)
    await _pause_pending(async_db_session, frozen.id)

    first = await failover_cold_backlog(frozen.id, async_db_session)
    dist_after_first = await _pending_counts(async_db_session, camp["id"])

    second = await failover_cold_backlog(frozen.id, async_db_session)
    dist_after_second = await _pending_counts(async_db_session, camp["id"])

    assert first == 3, "first call moves the whole cold backlog"
    assert second == 0, "second call must move 0 rows"
    assert dist_after_second == dist_after_first, "distribution must be unchanged"


# ─── FAIL-07 / D-13 ───────────────────────────────────────────────────────────

async def test_failover_no_receiver_keeps_paused(
    async_db_session, test_running_campaign_factory, test_queue_item_factory,
):
    """FAIL-07 / D-13: no healthy receiver (the frozen sender is the only one) → return 0,
    rows stay on the frozen sender with their paused scheduled_at unchanged; nothing lost."""
    from app.services.failover import failover_cold_backlog

    camp, senders = await test_running_campaign_factory(sender_count=1)
    frozen = senders[0]

    phones = [f"+7990097{i:04d}" for i in range(2)]
    for ph in phones:
        await test_queue_item_factory(camp["id"], frozen.id, ph, status="pending",
                                      with_cca=True, with_conversation=False)

    await _freeze_sender(async_db_session, frozen.id)
    await _pause_pending(async_db_session, frozen.id)

    # Capture the paused scheduled_at to prove it is left untouched.
    paused_before = {ph: await _scheduled_at(async_db_session, camp["id"], ph) for ph in phones}

    moved = await failover_cold_backlog(frozen.id, async_db_session)

    assert moved == 0, "no healthy receiver → nothing moved"
    after = await _pending_counts(async_db_session, camp["id"])
    assert after.get(str(frozen.id), 0) == 2, "all rows stay on the frozen sender"
    for ph in phones:
        assert await _sender_of(async_db_session, camp["id"], ph) == str(frozen.id)
        assert await _scheduled_at(async_db_session, camp["id"], ph) == paused_before[ph], (
            "paused scheduled_at must remain unchanged (reconcile-resume handles it later)"
        )


# ─── FAIL-08 ──────────────────────────────────────────────────────────────────

async def test_failover_logs_count_no_pii(
    async_db_session, test_running_campaign_factory, test_queue_item_factory, caplog,
):
    """FAIL-08: failover logs the moved COUNT and sender UUID(s) but NEVER a recipient
    phone (PII discipline — CLAUDE.md: no PII in logs)."""
    import logging
    from app.services.failover import failover_cold_backlog

    camp, senders = await test_running_campaign_factory(sender_count=2)
    frozen, healthy = senders[0], senders[1]

    secret_phone = "+79900980777"
    await test_queue_item_factory(camp["id"], frozen.id, secret_phone, status="pending",
                                  with_cca=True, with_conversation=False)

    await _freeze_sender(async_db_session, frozen.id)
    await _pause_pending(async_db_session, frozen.id)

    with caplog.at_level(logging.INFO):
        moved = await failover_cold_backlog(frozen.id, async_db_session)

    assert moved == 1
    assert secret_phone not in caplog.text, "recipient phone (PII) must NEVER appear in logs"
    # The audit log must carry the moved COUNT and the frozen sender UUID.
    assert "1" in caplog.text, "log must contain the moved count"
    assert str(frozen.id) in caplog.text, "log must contain the source (frozen) sender UUID"
