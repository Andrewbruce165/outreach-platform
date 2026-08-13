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


# ─── Evacuation freeze-state helpers (copied verbatim from test_failover.py:62-93) ─

async def _freeze_sender(db, sender_id, status: str = "spam_limited"):
    """Flag a sender restricted exactly as the freeze paths do (queue.py / listener.py).

    The freeze write MUST land before rebalance runs so the eligible-pool filter
    (restriction_status='none') excludes this sender as a receiver.
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
    failover/rebalance (queue.py:745 / listener.py:926). Evacuated rows must be
    reset to NOW()."""
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


# ─── EVAC-01 ─────────────────────────────────────────────────────────────────

async def test_rebalance_evacuates_frozen_donor_fully(
    async_db_session, test_running_campaign_factory, test_queue_item_factory,
    test_sender_factory, attach_sender_to_campaign,
):
    """EVAC-01: a campaign whose ONLY sender is frozen (the old P<2 no-op stranded
    the backlog) still evacuates 100% of its cold-pending rows onto a freshly-
    attached healthy sender. This is the exact production failure being fixed:
    the frozen donor is excluded from the eligible pool → P=1 → old code returned
    0; full evacuation moves every cold row regardless of fair-share math."""
    from app.services.rebalance import rebalance_on_attach

    camp, senders = await test_running_campaign_factory(sender_count=1)
    frozen = senders[0]

    phones = [f"+7990050{i:04d}" for i in range(4)]
    for ph in phones:
        await test_queue_item_factory(camp["id"], frozen.id, ph, status="pending",
                                      with_cca=True, with_conversation=False)

    # Freeze the only sender + pause its pending +24h (what the freeze path does).
    await _freeze_sender(async_db_session, frozen.id)
    await _pause_pending(async_db_session, frozen.id)

    # Attach a fresh healthy sender, then rebalance onto it.
    healthy = await test_sender_factory()
    await attach_sender_to_campaign(camp["id"], healthy.id)

    moved = await rebalance_on_attach(camp["id"], healthy.id, async_db_session)

    after = await _pending_counts(async_db_session, camp["id"])
    assert moved == 4, "all 4 cold-pending rows must evacuate off the frozen sender"
    assert sum(after.values()) == 4, "rebalance must not create or drop rows"
    assert after.get(str(frozen.id), 0) == 0, "frozen sender holds 0 pending after"
    assert after.get(str(healthy.id), 0) == 4, "healthy sender holds all 4 evacuated rows"
    # Every moved recipient's sticky CCA repoints at the healthy sender.
    for ph in phones:
        assert await _cca_sender_for(async_db_session, camp["id"], ph) == str(healthy.id)


# ─── EVAC-02 ─────────────────────────────────────────────────────────────────

async def test_rebalance_resets_scheduled_at_on_evacuation(
    async_db_session, test_running_campaign_factory, test_queue_item_factory,
    test_sender_factory, attach_sender_to_campaign,
):
    """EVAC-02: evacuated rows shed the inherited +24h PEER_FLOOD pause — their
    scheduled_at is reset to NOW() so the healthy receiver can send immediately."""
    from app.services.rebalance import rebalance_on_attach

    camp, senders = await test_running_campaign_factory(sender_count=1)
    frozen = senders[0]

    phones = [f"+7990051{i:04d}" for i in range(4)]
    for ph in phones:
        await test_queue_item_factory(camp["id"], frozen.id, ph, status="pending",
                                      with_cca=True, with_conversation=False)
    await _freeze_sender(async_db_session, frozen.id)
    await _pause_pending(async_db_session, frozen.id)  # scheduled_at → NOW()+24h

    healthy = await test_sender_factory()
    await attach_sender_to_campaign(camp["id"], healthy.id)

    moved = await rebalance_on_attach(camp["id"], healthy.id, async_db_session)
    assert moved == 4

    horizon = (await async_db_session.execute(
        text("SELECT NOW() + INTERVAL '1 hour'")
    )).scalar()
    for ph in phones:
        sched = await _scheduled_at(async_db_session, camp["id"], ph)
        assert sched is not None and sched < horizon, (
            "evacuated row scheduled_at must be reset to NOW() (not the +24h pause)"
        )


# ─── EVAC idempotent ─────────────────────────────────────────────────────────

async def test_rebalance_evacuation_idempotent(
    async_db_session, test_running_campaign_factory, test_queue_item_factory,
    test_sender_factory, attach_sender_to_campaign,
):
    """A repeated rebalance after a full evacuation moves 0 rows and leaves the
    distribution unchanged (idempotent — the rows now sit on the eligible pool)."""
    from app.services.rebalance import rebalance_on_attach

    camp, senders = await test_running_campaign_factory(sender_count=1)
    frozen = senders[0]

    for i in range(4):
        await test_queue_item_factory(camp["id"], frozen.id, f"+7990052{i:04d}",
                                      status="pending", with_cca=True,
                                      with_conversation=False)
    await _freeze_sender(async_db_session, frozen.id)
    await _pause_pending(async_db_session, frozen.id)

    healthy = await test_sender_factory()
    await attach_sender_to_campaign(camp["id"], healthy.id)

    first = await rebalance_on_attach(camp["id"], healthy.id, async_db_session)
    dist_after_first = await _pending_counts(async_db_session, camp["id"])

    second = await rebalance_on_attach(camp["id"], healthy.id, async_db_session)
    dist_after_second = await _pending_counts(async_db_session, camp["id"])

    assert first == 4, "first call evacuates the whole cold backlog"
    assert second == 0, "second call must move 0 rows"
    assert dist_after_second == dist_after_first, "distribution must be unchanged"


# ═══ EVEN-split: continuous rebalance across ALL eligible senders ═════════════
# (debug: campaign-pending-not-on-idle-senders, 2026-07-10 — idle healthy senders
#  were never topped up from the standing backlog because rebalance_on_attach is
#  edge-triggered and one-sender-only.)


async def test_even_split_backfills_idle_eligible_senders(
    async_db_session, test_running_campaign_factory, test_queue_item_factory,
):
    """EVEN-01: A holds the whole cold backlog, B and C are eligible but idle at 0.
    rebalance_campaign_even evens the pool to total/P each and keeps CCA in sync."""
    from app.services.rebalance import rebalance_campaign_even

    camp, senders = await test_running_campaign_factory(sender_count=3)
    a, b, c = senders[0], senders[1], senders[2]

    phones = [f"+7990070{i:04d}" for i in range(6)]
    for ph in phones:
        await test_queue_item_factory(camp["id"], a.id, ph, status="pending",
                                      with_cca=True, with_conversation=False)

    moved = await rebalance_campaign_even(camp["id"], async_db_session)

    after = await _pending_counts(async_db_session, camp["id"])
    assert moved == 4, "4 of A's 6 rows must move (2 to B, 2 to C)"
    assert sum(after.values()) == 6, "even-split must not create or drop rows"
    assert after.get(str(a.id), 0) == 2
    assert after.get(str(b.id), 0) == 2
    assert after.get(str(c.id), 0) == 2

    # CCA sync invariant: every recipient's CCA matches its queue row's sender.
    for ph in phones:
        row = (await async_db_session.execute(text("""
            SELECT sender_id FROM message_queue
            WHERE campaign_id = :cid AND recipient_phone = :phone AND status = 'pending'
        """), {"cid": str(camp["id"]), "phone": ph})).first()
        assert row is not None
        assert await _cca_sender_for(async_db_session, camp["id"], ph) == str(row[0])


async def test_even_split_idempotent(
    async_db_session, test_running_campaign_factory, test_queue_item_factory,
):
    """EVEN-02: a second even-split pass moves 0 rows and leaves the distribution
    unchanged (minimal-move targets: an already-even pool has zero surplus)."""
    from app.services.rebalance import rebalance_campaign_even

    camp, senders = await test_running_campaign_factory(sender_count=3)
    a = senders[0]

    for i in range(7):  # total=7, P=3 → targets 3/2/2 (remainder stays put)
        await test_queue_item_factory(camp["id"], a.id, f"+7990071{i:04d}",
                                      status="pending", with_cca=True,
                                      with_conversation=False)

    first = await rebalance_campaign_even(camp["id"], async_db_session)
    dist_after_first = await _pending_counts(async_db_session, camp["id"])

    second = await rebalance_campaign_even(camp["id"], async_db_session)
    dist_after_second = await _pending_counts(async_db_session, camp["id"])

    assert first == 4, "7 rows on A → A keeps ceil(7/3)=3, moves 2+2"
    assert second == 0, "second pass must move 0 rows"
    assert dist_after_second == dist_after_first, "distribution must be unchanged"


async def test_even_split_preserves_scheduled_at(
    async_db_session, test_running_campaign_factory, test_queue_item_factory,
):
    """EVEN-03: moved rows keep their scheduled_at (NO reset — donors are healthy,
    so there is no inherited freeze-pause to shed; contrast with evacuation)."""
    from app.services.rebalance import rebalance_campaign_even

    camp, senders = await test_running_campaign_factory(sender_count=2)
    a, b = senders[0], senders[1]

    phones = [f"+7990072{i:04d}" for i in range(4)]
    for ph in phones:
        await test_queue_item_factory(camp["id"], a.id, ph, status="pending",
                                      with_cca=True, with_conversation=False)
    # Give A's rows a distinctive FUTURE scheduled_at — a reset would clobber it.
    await async_db_session.execute(text("""
        UPDATE message_queue SET scheduled_at = NOW() + INTERVAL '3 hours'
        WHERE campaign_id = :cid AND sender_id = :sid AND status = 'pending'
    """), {"cid": str(camp["id"]), "sid": str(a.id)})
    await async_db_session.commit()

    moved = await rebalance_campaign_even(camp["id"], async_db_session)
    assert moved == 2, "P=2, total=4 → 2 rows move to B"

    horizon = (await async_db_session.execute(
        text("SELECT NOW() + INTERVAL '2 hours'")
    )).scalar()
    rows = (await async_db_session.execute(text("""
        SELECT scheduled_at FROM message_queue
        WHERE campaign_id = :cid AND sender_id = :sid AND status = 'pending'
    """), {"cid": str(camp["id"]), "sid": str(b.id)})).fetchall()
    assert len(rows) == 2
    for r in rows:
        assert r[0] > horizon, (
            "even-split must PRESERVE scheduled_at (no NOW() reset for healthy donors)"
        )


async def test_even_split_skips_non_cold(
    async_db_session, test_running_campaign_factory, test_queue_item_factory,
):
    """EVEN-04: sent / processing / engaged rows never move — only true cold-pending
    rows participate in the even-split (same predicate as rebalance_on_attach)."""
    from app.services.rebalance import rebalance_campaign_even

    camp, senders = await test_running_campaign_factory(sender_count=2)
    a, b = senders[0], senders[1]

    sent_phone = "+79900730001"
    processing_phone = "+79900730002"
    engaged_phone = "+79900730003"   # pending BUT has a conversation
    cold_1 = "+79900730004"
    cold_2 = "+79900730005"

    await test_queue_item_factory(camp["id"], a.id, sent_phone, status="sent",
                                  with_cca=True, with_conversation=False)
    await test_queue_item_factory(camp["id"], a.id, processing_phone,
                                  status="processing", with_cca=True,
                                  with_conversation=False)
    await test_queue_item_factory(camp["id"], a.id, engaged_phone, status="pending",
                                  with_cca=True, with_conversation=True)
    await test_queue_item_factory(camp["id"], a.id, cold_1, status="pending",
                                  with_cca=True, with_conversation=False)
    await test_queue_item_factory(camp["id"], a.id, cold_2, status="pending",
                                  with_cca=True, with_conversation=False)

    moved = await rebalance_campaign_even(camp["id"], async_db_session)
    assert moved == 1, "cold total=2, P=2 → exactly 1 cold row moves to B"

    async def _sender_of(phone, status):
        row = (await async_db_session.execute(text("""
            SELECT sender_id FROM message_queue
            WHERE campaign_id = :cid AND recipient_phone = :phone AND status = :st
        """), {"cid": str(camp["id"]), "phone": phone, "st": status})).first()
        return str(row[0]) if row else None

    assert await _sender_of(sent_phone, "sent") == str(a.id)
    assert await _sender_of(processing_phone, "processing") == str(a.id)
    assert await _sender_of(engaged_phone, "pending") == str(a.id)
    assert await _cca_sender_for(async_db_session, camp["id"], engaged_phone) == str(a.id)


async def test_even_split_never_targets_ineligible_sender(
    async_db_session, test_running_campaign_factory, test_queue_item_factory,
):
    """EVEN-05: a restricted attached sender is neither donor nor receiver — the
    even-split runs over the ELIGIBLE pool only (frozen C gets 0; B gets its half)."""
    from app.services.rebalance import rebalance_campaign_even

    camp, senders = await test_running_campaign_factory(sender_count=3)
    a, b, frozen = senders[0], senders[1], senders[2]

    for i in range(6):
        await test_queue_item_factory(camp["id"], a.id, f"+7990074{i:04d}",
                                      status="pending", with_cca=True,
                                      with_conversation=False)
    await _freeze_sender(async_db_session, frozen.id)

    moved = await rebalance_campaign_even(camp["id"], async_db_session)

    after = await _pending_counts(async_db_session, camp["id"])
    assert moved == 3, "eligible P=2 → half of A's 6 rows move to B"
    assert after.get(str(a.id), 0) == 3
    assert after.get(str(b.id), 0) == 3
    assert after.get(str(frozen.id), 0) == 0, "frozen sender must receive nothing"


async def test_even_split_noop_below_two_eligible(
    async_db_session, test_running_campaign_factory, test_queue_item_factory,
):
    """EVEN-06: with fewer than 2 eligible senders there is nothing to even out —
    returns 0 and touches nothing (stranded-on-ineligible rows are the sweep's job)."""
    from app.services.rebalance import rebalance_campaign_even

    camp, senders = await test_running_campaign_factory(sender_count=1)
    a = senders[0]

    for i in range(3):
        await test_queue_item_factory(camp["id"], a.id, f"+7990075{i:04d}",
                                      status="pending", with_cca=True,
                                      with_conversation=False)

    before = await _pending_counts(async_db_session, camp["id"])
    moved = await rebalance_campaign_even(camp["id"], async_db_session)
    after = await _pending_counts(async_db_session, camp["id"])

    assert moved == 0, "P<2 → no-op"
    assert after == before, "distribution must be unchanged"


# ─── 2026-08-13 resolve-carousel regression: rotation-pinned rows never move ──

async def _stamp_rotation_pin(db, campaign_id, phone, tried_sender_ids):
    """Stamp a queue row exactly as queue._reroute_resolve_fail does — the
    presence of extra_data.nr_tried_senders pins the row to its rotation-chosen
    sender."""
    import json as _json
    await db.execute(text("""
        UPDATE message_queue
        SET extra_data = CAST(:ed AS JSONB)
        WHERE campaign_id = :cid AND recipient_phone = :phone AND status = 'pending'
    """), {
        "ed": _json.dumps({"nr_tried_senders": [str(s) for s in tried_sender_ids]}),
        "cid": str(campaign_id), "phone": phone,
    })
    await db.commit()


async def _sender_of(db, campaign_id, phone):
    row = (await db.execute(text("""
        SELECT sender_id FROM message_queue
        WHERE campaign_id = :cid AND recipient_phone = :phone AND status = 'pending'
    """), {"cid": str(campaign_id), "phone": phone})).first()
    return str(row[0]) if row else None


async def test_even_split_skips_rotation_pinned_rows(
    async_db_session, test_running_campaign_factory, test_queue_item_factory,
):
    """CAROUSEL-01: a row mid resolve-rotation (nr_tried_senders stamped) is
    pinned to its rotation-chosen sender — even-split must neither move it nor
    count it into the load math. Without this, rebalance yanked rerouted rows
    back onto already-tried senders and the resolve carousel spun forever
    (2026-08-13 incident: ~762 live resolves/hour, tried-list frozen)."""
    from app.services.rebalance import rebalance_campaign_even

    camp, senders = await test_running_campaign_factory(sender_count=2)
    a, b = senders[0], senders[1]

    plain = [f"+7990076{i:04d}" for i in range(4)]
    pinned = ["+79900769001", "+79900769002"]
    for ph in [*plain, *pinned]:
        await test_queue_item_factory(camp["id"], a.id, ph, status="pending",
                                      with_cca=True, with_conversation=False)
    for ph in pinned:
        await _stamp_rotation_pin(async_db_session, camp["id"], ph, [b.id])

    moved = await rebalance_campaign_even(camp["id"], async_db_session)

    # Only the 4 unpinned rows participate: total=4, P=2 → 2 move to B.
    assert moved == 2, "pinned rows must be excluded from the even-split economy"
    for ph in pinned:
        assert await _sender_of(async_db_session, camp["id"], ph) == str(a.id), (
            "rotation-pinned row must stay on its rotation-chosen sender"
        )
    after = await _pending_counts(async_db_session, camp["id"])
    assert after.get(str(b.id), 0) == 2, "B receives only unpinned rows"


async def test_attach_backfill_skips_rotation_pinned_rows(
    async_db_session, test_running_campaign_factory, test_queue_item_factory,
):
    """CAROUSEL-02: same pin guard on the attach back-fill path (shared movable
    predicate, incl. the mq2 donor-load subquery) — the newly-attached sender is
    back-filled from UNPINNED rows only; pinned rows stay put."""
    from app.services.rebalance import rebalance_on_attach

    camp, senders = await test_running_campaign_factory(sender_count=2)
    a, b = senders[0], senders[1]

    plain = [f"+7990077{i:04d}" for i in range(4)]
    pinned = ["+79900779001", "+79900779002"]
    for ph in [*plain, *pinned]:
        await test_queue_item_factory(camp["id"], a.id, ph, status="pending",
                                      with_cca=True, with_conversation=False)
    for ph in pinned:
        await _stamp_rotation_pin(async_db_session, camp["id"], ph, [b.id])

    await rebalance_on_attach(camp["id"], b.id, async_db_session)
    await async_db_session.commit()

    for ph in pinned:
        assert await _sender_of(async_db_session, camp["id"], ph) == str(a.id), (
            "rotation-pinned row must not be donated to the newly-attached sender"
        )
    after = await _pending_counts(async_db_session, camp["id"])
    # Unpinned total=4, P=2 → B back-fills to ceil(4/2)=2; pinned 2 stay on A.
    assert after.get(str(b.id), 0) == 2
    assert after.get(str(a.id), 0) == 4
