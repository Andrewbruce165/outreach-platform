"""Phase 8 — campaign-scoped even-split rebalance on sender attach (D-08/D-09).

Why this exists: contacts are assigned to a sender at enqueue time (sticky
`campaign_contact_assignments`, D-07). Least-loaded rotation alone therefore
never back-fills a sender that is attached AFTER a folder has been fully
enqueued — without a rebalance, a sender added to a running campaign would
receive zero traffic.

`rebalance_on_attach` performs a single, set-based, campaign-scoped pass that
moves a fair share of *un-sent cold-pending* queue rows from overloaded senders
onto the newly-attached sender, keeping `campaign_contact_assignments` in sync
and never racing the queue worker.

Concurrency safety (mirrors queue.py:294-313): the donor SELECT uses
`status = 'pending'` + `FOR UPDATE OF mq SKIP LOCKED` — the same discipline the
worker uses to claim rows. The worker flips a claimed row to `processing` and
commits BEFORE hitting Telegram, so a mid-send row is excluded by the status
guard. The queue UPDATE + CCA UPDATE happen in ONE transaction, so an observer
never sees `message_queue.sender_id` and `campaign_contact_assignments.sender_id`
disagree.

Scope note (D-08, intentional v1 limits): the ±1-of-total/P even-split is
guaranteed for the NEWLY-ATTACHED sender only. This single pass does not
re-balance pre-existing donors against each other, and the `total/P > BATCH_CAP`
case would need a follow-up pass — both are out of v1 scope (current campaigns
run with a single sender; the move is a cheap UPDATE).

`_pick_least_loaded` (rotation.py) is deliberately NOT reused: it counts load
GLOBALLY across all campaigns and returns a single sender. We need a
campaign-scoped count and a set-based move.
"""

import logging
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.rotation import _pick_least_loaded

logger = logging.getLogger(__name__)

# Max rows moved in one rebalance pass (Claude's discretion per D-09). A single
# pass is fine at v1 scale; this caps the transaction size. If a pool ever has
# total/P > BATCH_CAP cold-pending, a follow-up attach/rebalance evens it further.
BATCH_CAP = 500

# Movable cold-pending predicate (RESEARCH §"never sent / no dialog"), keyed on
# recipient_phone — same identity key as campaign_contact_assignments.contact_phone
# and conversations.contact_phone. A pending row is movable iff the recipient was
# never successfully sent to in this campaign AND has no started conversation.
_COLD_PENDING_PREDICATE = """
    mq.status = 'pending'
    AND mq.campaign_id = :cid
    AND NOT EXISTS (
        SELECT 1 FROM message_queue s
        WHERE s.campaign_id = mq.campaign_id
          AND s.recipient_phone = mq.recipient_phone
          AND s.status = 'sent'
    )
    AND NOT EXISTS (
        SELECT 1 FROM conversations cv
        WHERE cv.workspace_id = mq.workspace_id
          AND cv.contact_phone = mq.recipient_phone
    )
"""


async def rebalance_on_attach(
    campaign_id, new_sender_id, db: AsyncSession
) -> int:
    """Move a fair share of cold-pending rows onto a newly-attached sender.

    Campaign-scoped even-split: after this call the NEWLY-ATTACHED sender holds
    within ±1 of total/P cold-pending rows (one-directional back-fill). Idempotent
    (re-running on an already-even pool moves 0 rows) and worker-safe.

    TRANSACTION-NEUTRAL (CR-01): this function does NOT commit. The CALLER owns the
    transaction and commits exactly once after this call returns, so the rebalance
    row moves land in the same atomic commit as the attach itself (campaigns.py:
    attach_sender). Tests that call this directly must commit themselves or assert
    through the same session.

    Args:
        campaign_id: the campaign whose pool is being rebalanced.
        new_sender_id: the just-attached sender to back-fill.
        db: async session; the CALLER owns the transaction (no commit here).

    Returns:
        The number of cold-pending rows moved onto ``new_sender_id`` (0 if the
        pool is already balanced, the new sender is ineligible, the pool has
        fewer than 2 eligible senders, or there is nothing movable).
    """
    cid = str(campaign_id)
    new_sid = str(new_sender_id)

    # Step 1: resolve the workspace + eligible pool. The eligible-candidate
    # filter is copied verbatim from rotation.py:113-123 so a spam_limited /
    # frozen / non-sender account never receives moved rows. workspace_id scope
    # (via campaigns) closes the cross-workspace IDOR surface (threat T1).
    pool_rows = (await db.execute(
        text("""
            SELECT s.id AS sid
            FROM campaign_senders cs
            JOIN senders s ON s.id = cs.sender_id
            JOIN campaigns c ON c.id = cs.campaign_id
            WHERE cs.campaign_id = :cid
              AND s.lifecycle_status = 'active'
              AND s.auth_status = 'ok'
              AND s.role = 'sender'
              AND s.restriction_status = 'none'
              AND s.workspace_id = c.workspace_id
        """),
        {"cid": cid},
    )).fetchall()
    pool = {str(r.sid) for r in pool_rows}

    if new_sid not in pool:
        # Ineligible (or not actually attached) new sender → nothing to do (T3).
        return 0

    pool_ids = list(pool)  # eligible sender ids; new_sid is guaranteed in it.

    # ── Step 1b: FULL EVACUATION of ineligible-donor cold rows (EVAC-01/02/03) ──
    # BEFORE the fair-share math runs, move EVERY cold-pending row that currently
    # sits on an INELIGIBLE sender (any sender NOT in the eligible pool: restricted
    # / frozen / paused / auth-failed / detached) onto the eligible pool, resetting
    # scheduled_at = NOW(). This closes three production root causes the fair-share
    # pass could not (proven stuck in prod 2026-07-06):
    #   #2  P<2 no-op — a campaign whose only prior sender froze leaves P=1 (the
    #       frozen donor is excluded from `pool`), so fair-share returned 0 and the
    #       backlog stranded. Evacuation is independent of P (>=1 recipient suffices).
    #   #3  partial-share / ORDER BY scheduled_at DESC left the now-due row on the
    #       frozen sender. Evacuation drains ALL ineligible-donor rows, not a share.
    #   #4  no scheduled_at reset — the inherited +24h PEER_FLOOD pause travelled
    #       with the moved row. Evacuation resets scheduled_at = NOW() (mirrors
    #       failover.py:199-217) so the healthy receiver can send immediately.
    # Worker-safe: same status='pending' + FOR UPDATE OF mq SKIP LOCKED discipline
    # as the rest of the module — a row the worker is mid-sending is either locked
    # (SKIP LOCKED skips it) or already flipped to 'processing' (excluded by the
    # status guard in _COLD_PENDING_PREDICATE). CR-01: still no commit here.
    claimed = (await db.execute(
        text(f"""
            SELECT mq.id AS id, mq.recipient_phone AS phone
            FROM message_queue mq
            WHERE {_COLD_PENDING_PREDICATE}
              AND mq.sender_id IS NOT NULL
              AND NOT (mq.sender_id = ANY(:pool_ids))
            FOR UPDATE OF mq SKIP LOCKED
        """),
        {"cid": cid, "pool_ids": pool_ids},
    )).fetchall()

    evacuated = 0
    for row in claimed:
        # P=1 → always new_sid; P>=2 → spread across the eligible pool.
        recv = str(await _pick_least_loaded(db, pool_ids))
        await db.execute(
            text("""
                UPDATE message_queue
                SET sender_id = :recv, scheduled_at = NOW()
                WHERE id = :rid
            """),
            {"recv": recv, "rid": str(row.id)},
        )
        await db.execute(
            text("""
                UPDATE campaign_contact_assignments
                SET sender_id = :recv
                WHERE campaign_id = :cid AND contact_phone = :phone
            """),
            {"recv": recv, "cid": cid, "phone": row.phone},
        )
        evacuated += 1
    if evacuated:
        # Log COUNT + campaign UUID ONLY — never recipient phones (CLAUDE.md PII,
        # threat T4; copy failover.py:222 log shape).
        logger.info(
            "rebalance: evacuated %d cold-pending rows off ineligible senders "
            "in campaign %s",
            evacuated, cid,
        )

    async def _fair_share_backfill() -> int:
        """Original Phase-8 even-split back-fill of the newly-attached sender.

        Runs AFTER evacuation, over the eligible pool. Because evacuation moved the
        ineligible-donor rows onto eligible senders in the SAME session, the load
        query below re-reads them on their new senders → need<=0 → 0 in the
        already-balanced case, so evacuation + fair-share never double-move a row
        (idempotent).
        """
        P = len(pool)
        if P < 2:
            return 0

        # Step 2: count current movable cold-pending load per sender (campaign-scoped).
        load_rows = (await db.execute(
            text(f"""
                SELECT mq.sender_id AS sid, COUNT(*) AS cnt
                FROM message_queue mq
                WHERE {_COLD_PENDING_PREDICATE}
                GROUP BY mq.sender_id
            """),
            {"cid": cid},
        )).fetchall()
        load = {str(r.sid): int(r.cnt) for r in load_rows}
        total = sum(load.values())
        if total == 0:
            return 0

        # Step 3: fair share for the new sender (ceil) and donor threshold (floor).
        # CR-02: use CEIL for the recipient's goal and FLOOR for the donor threshold.
        # If both used floor (total // P) and total < P (e.g. P=3, total=2), the new
        # sender's target would be 0 → need=0 → it gets starved while a donor hoards
        # the whole backlog — the exact failure this module exists to prevent. Ceil
        # for the recipient guarantees it pulls at least 1 row when a surplus exists;
        # floor for the donor threshold keeps us from over-draining donors below their
        # fair floor. (P=3, total=2 → fair_share=ceil(2/3)=1, floor_target=0 → B=1,
        # A=1, C=0.)
        fair_share = (total + P - 1) // P  # == ceil(total / P), integer-only
        need = fair_share - load.get(new_sid, 0)
        if need <= 0:
            # Already balanced — idempotent no-op.
            return 0
        need = min(need, BATCH_CAP)

        # Donor senders are those above the floor target (their surplus is movable).
        floor_target = total // P
        donors = [sid for sid, cnt in load.items()
                  if cnt > floor_target and sid != new_sid]
        if not donors:
            return 0

        # Step 4: claim donor rows under the SAME lock discipline as the worker
        # (queue.py:313) — FOR UPDATE OF mq SKIP LOCKED + status='pending'. This is
        # what prevents racing the worker (threat T2): a row the worker is sending is
        # either locked (SKIP LOCKED skips it) or already flipped to 'processing'
        # (the status guard in _COLD_PENDING_PREDICATE excludes it).
        # ORDER BY donor-load DESC, scheduled_at DESC: drain the most overloaded
        # donors first and move the rows scheduled latest, minimizing the chance of
        # racing an imminent send.
        moved_rows = (await db.execute(
            text(f"""
                SELECT mq.id AS id, mq.recipient_phone AS phone
                FROM message_queue mq
                JOIN (
                    SELECT mq2.sender_id AS sid, COUNT(*) AS cnt
                    FROM message_queue mq2
                    WHERE {_COLD_PENDING_PREDICATE.replace('mq.', 'mq2.')}
                    GROUP BY mq2.sender_id
                ) dl ON dl.sid = mq.sender_id
                WHERE {_COLD_PENDING_PREDICATE}
                  AND mq.sender_id = ANY(:donors)
                ORDER BY dl.cnt DESC, mq.scheduled_at DESC
                LIMIT :need
                FOR UPDATE OF mq SKIP LOCKED
            """),
            {"cid": cid, "donors": donors, "need": need},
        )).fetchall()

        if not moved_rows:
            return 0

        # Step 5: reassign in the SAME transaction — queue row + sticky CCA in
        # lock-step (Pitfall 3). CCA is keyed (campaign_id, contact_phone) UNIQUE.
        for row in moved_rows:
            await db.execute(
                text("UPDATE message_queue SET sender_id = :new WHERE id = :rid"),
                {"new": new_sid, "rid": str(row.id)},
            )
            await db.execute(
                text("""
                    UPDATE campaign_contact_assignments
                    SET sender_id = :new
                    WHERE campaign_id = :cid AND contact_phone = :phone
                """),
                {"new": new_sid, "cid": cid, "phone": row.phone},
            )

        # CR-01: no commit here — the caller owns the transaction (single commit).
        n = len(moved_rows)
        # Log COUNT ONLY — never recipient phones / payloads (CLAUDE.md, threat T4).
        logger.info(
            "rebalance: moved %d cold-pending rows to sender %s in campaign %s",
            n, new_sid, cid,
        )
        return n

    return evacuated + await _fair_share_backfill()
