"""Phase 8 — Rebalance-on-attach service tests (Wave 0 RED stubs).

These tests fully ASSERT the behaviour of `app.services.rebalance.rebalance_on_attach`
which Plan 08-02 will implement. The module does not exist yet, so the import is done
INSIDE each test body — that keeps `pytest --collect-only` clean (no top-level
ImportError collection error) while the tests still ERROR/FAIL RED at run time. This is
the expected Wave-0 state per 08-VALIDATION.md.

Test → requirement map (contract — names consumed by later verify commands):
- test_rebalance_evens_cold_pending → POOL-07 (skewed backlog → newly-attached sender
      gets a pending bucket within ±1 of total/P; P=2 so the whole pool evens out)
- test_rebalance_idempotent         → POOL-08 (second call moves 0 rows, distribution unchanged)
- test_rebalance_skips_non_cold     → POOL-08b (never moves sent/processing/engaged rows)

Rationale scoping (D-08): the ±1 guarantee is for the NEW sender's back-fill, not a full
re-even of pre-existing donors against each other; a single pass caps at BATCH_CAP=500.
We use P=2 here so the back-fill of B necessarily evens the whole pool.
"""

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


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


# ─── POOL-07 ─────────────────────────────────────────────────────────────────

async def test_rebalance_evens_cold_pending(
    async_db_session, test_running_campaign_factory, test_queue_item_factory,
):
    """POOL-07: sender A holds a skewed cold-pending backlog, sender B newly attached.
    After rebalance_on_attach, B's pending bucket is within ±1 of total/P (P=2)."""
    from app.services.rebalance import rebalance_on_attach

    camp, senders = await test_running_campaign_factory(sender_count=2)
    a, b = senders[0], senders[1]

    # Sender A holds 6 cold-pending rows; B holds none.
    phones = [f"+7990010{i:04d}" for i in range(6)]
    for ph in phones:
        await test_queue_item_factory(camp["id"], a.id, ph, status="pending",
                                      with_cca=True, with_conversation=False)

    before = await _pending_counts(async_db_session, camp["id"])
    assert before.get(str(a.id), 0) == 6

    await rebalance_on_attach(camp["id"], b.id, async_db_session)

    after = await _pending_counts(async_db_session, camp["id"])
    total = sum(after.values())
    assert total == 6, "rebalance must not create or drop rows"
    target = total // 2  # P=2 → 3
    b_bucket = after.get(str(b.id), 0)
    assert abs(b_bucket - target) <= 1, f"B bucket {b_bucket} not within ±1 of {target}"

    # CCA sync invariant (D-08 Pitfall 3): every moved recipient's CCA points at B.
    moved_to_b = [
        ph for ph in phones
        if await _cca_sender_for(async_db_session, camp["id"], ph) == str(b.id)
    ]
    assert len(moved_to_b) == b_bucket
    for ph in moved_to_b:
        row = (await async_db_session.execute(text("""
            SELECT sender_id FROM message_queue
            WHERE campaign_id = :cid AND recipient_phone = :phone AND status = 'pending'
        """), {"cid": str(camp["id"]), "phone": ph})).first()
        assert row is not None and str(row[0]) == str(b.id), (
            "moved queue row sender must match its CCA sender (in sync)"
        )


# ─── POOL-08 ─────────────────────────────────────────────────────────────────

async def test_rebalance_idempotent(
    async_db_session, test_running_campaign_factory, test_queue_item_factory,
):
    """POOL-08: a second rebalance_on_attach call moves 0 rows and leaves the
    distribution unchanged."""
    from app.services.rebalance import rebalance_on_attach

    camp, senders = await test_running_campaign_factory(sender_count=2)
    a, b = senders[0], senders[1]

    for i in range(6):
        await test_queue_item_factory(camp["id"], a.id, f"+7990020{i:04d}",
                                      status="pending", with_cca=True,
                                      with_conversation=False)

    await rebalance_on_attach(camp["id"], b.id, async_db_session)
    dist_after_first = await _pending_counts(async_db_session, camp["id"])

    moved = await rebalance_on_attach(camp["id"], b.id, async_db_session)
    dist_after_second = await _pending_counts(async_db_session, camp["id"])

    assert moved == 0, "second rebalance must move 0 rows"
    assert dist_after_second == dist_after_first, "distribution must be unchanged"


# ─── POOL-08b ────────────────────────────────────────────────────────────────

async def test_rebalance_skips_non_cold(
    async_db_session, test_running_campaign_factory, test_queue_item_factory,
):
    """POOL-08b: rebalance NEVER moves sent / processing / engaged rows — only true
    cold-pending rows are eligible."""
    from app.services.rebalance import rebalance_on_attach

    camp, senders = await test_running_campaign_factory(sender_count=2)
    a, b = senders[0], senders[1]

    sent_phone = "+79900300001"
    processing_phone = "+79900300002"
    engaged_phone = "+79900300003"      # pending BUT has a conversation (engaged)
    cold_phone = "+79900300004"          # the only truly-movable row

    await test_queue_item_factory(camp["id"], a.id, sent_phone, status="sent",
                                  with_cca=True, with_conversation=False)
    await test_queue_item_factory(camp["id"], a.id, processing_phone,
                                  status="processing", with_cca=True,
                                  with_conversation=False)
    await test_queue_item_factory(camp["id"], a.id, engaged_phone, status="pending",
                                  with_cca=True, with_conversation=True)
    await test_queue_item_factory(camp["id"], a.id, cold_phone, status="pending",
                                  with_cca=True, with_conversation=False)

    await rebalance_on_attach(camp["id"], b.id, async_db_session)

    async def _sender_of(phone, status):
        row = (await async_db_session.execute(text("""
            SELECT sender_id FROM message_queue
            WHERE campaign_id = :cid AND recipient_phone = :phone AND status = :st
        """), {"cid": str(camp["id"]), "phone": phone, "st": status})).first()
        return str(row[0]) if row else None

    # sent / processing / engaged rows must remain with A (never moved).
    assert await _sender_of(sent_phone, "sent") == str(a.id)
    assert await _sender_of(processing_phone, "processing") == str(a.id)
    assert await _sender_of(engaged_phone, "pending") == str(a.id)
    # CCA for those must also be untouched.
    assert await _cca_sender_for(async_db_session, camp["id"], sent_phone) == str(a.id)
    assert await _cca_sender_for(async_db_session, camp["id"], engaged_phone) == str(a.id)


# ─── POOL-07 (CR-02 small-backlog regression) ────────────────────────────────

async def test_rebalance_p3_small_backlog_not_starved(
    async_db_session, test_running_campaign_factory, test_queue_item_factory,
):
    """CR-02: P=3, total=2 — the case the FLOOR-only fair share silently failed.

    With ``target = total // P`` (floor), P=3 and total=2 give target=0 → need=0,
    so the newly-attached sender B would be starved while donor A hoards the whole
    backlog — the exact failure this module exists to prevent. The ceil-for-
    recipient / floor-for-donor fix makes B pull ceil(2/3)=1 row, leaving A with 1
    and C with 0.
    """
    from app.services.rebalance import rebalance_on_attach

    camp, senders = await test_running_campaign_factory(sender_count=3)
    a, b, c = senders[0], senders[1], senders[2]

    # Donor A holds 2 cold-pending rows; B and C hold none. total=2 < P=3.
    phones = [f"+7990030{i:04d}" for i in range(2)]
    for ph in phones:
        await test_queue_item_factory(camp["id"], a.id, ph, status="pending",
                                      with_cca=True, with_conversation=False)

    before = await _pending_counts(async_db_session, camp["id"])
    assert before.get(str(a.id), 0) == 2

    moved = await rebalance_on_attach(camp["id"], b.id, async_db_session)

    after = await _pending_counts(async_db_session, camp["id"])
    assert sum(after.values()) == 2, "rebalance must not create or drop rows"
    assert moved == 1, "ceil(2/3)=1 row must move to B (B must not be starved)"
    assert after.get(str(b.id), 0) == 1, "B holds exactly 1 cold-pending row"
    assert after.get(str(a.id), 0) == 1, "A keeps exactly 1 (floor target)"
    assert after.get(str(c.id), 0) == 0, "C, not the attached sender, stays empty"

    # CCA stays in sync for the moved recipient.
    moved_to_b = [
        ph for ph in phones
        if await _cca_sender_for(async_db_session, camp["id"], ph) == str(b.id)
    ]
    assert len(moved_to_b) == 1, "exactly one recipient's CCA points at B"
