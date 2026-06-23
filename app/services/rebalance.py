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

    Args:
        campaign_id: the campaign whose pool is being rebalanced.
        new_sender_id: the just-attached sender to back-fill.
        db: async session; this function owns the transaction (single commit).

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

    # Step 3: fair-share target for the new sender (floor) and how many to pull.
    target = total // P
    need = target - load.get(new_sid, 0)
    if need <= 0:
        # Already balanced — idempotent no-op.
        return 0
    need = min(need, BATCH_CAP)

    # Donor senders are those above the target (their surplus is movable).
    donors = [sid for sid, cnt in load.items() if cnt > target and sid != new_sid]
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

    await db.commit()

    n = len(moved_rows)
    # Log COUNT ONLY — never recipient phones / payloads (CLAUDE.md, threat T4).
    logger.info(
        "rebalance: moved %d cold-pending rows to sender %s in campaign %s",
        n, new_sid, cid,
    )
    return n
