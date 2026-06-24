"""Phase 9 — Cold-contact failover off a frozen sender (FAIL-01..FAIL-09).

Why this exists: the b7cc7d06 incident — when a sender freezes (PEER_FLOOD /
ACCOUNT_FROZEN / antispam-signal) every freeze path pauses that sender's pending
queue +24h. Its *un-contacted cold backlog* then stalls for 24h on a dead
account instead of being picked up by the healthy pool. ``failover_cold_backlog``
moves that cold-pending backlog onto healthy pool senders inline at freeze time,
so cold contacts keep flowing while the frozen account is out.

What stays put (continuity, FAIL-03/FAIL-05): a contact who already received a
message in this campaign (sent/processing queue row) OR who has a *has-message*
conversation is ENGAGED — its pending row is left on the frozen sender so the
established dialog keeps replying once the soft limit lifts. Only truly cold
(never-sent, no started dialog) rows move. An EMPTY conversation (zero messages,
D-05) is still cold and IS moved.

Best-effort (FAIL-07 / D-13): if a campaign has no healthy receiver (the frozen
sender is the only pool member), nothing is moved — the rows stay paused on the
frozen sender and the existing reconcile-resume loop picks them up when the
restriction lifts. Nothing is ever lost or failed.

Divergence from rebalance.py (Pitfall 2 / EDIT 1): moved rows reset
``scheduled_at = NOW()`` so they are sendable immediately by the healthy
receiver — the +24h freeze pause must NOT travel with the row.

Selection (Pitfall 1): we do NOT call ``rotation.get_or_assign_sender`` — its
stale-CCA short-circuit (rotation.py:71-97) ignores ``restriction_status`` and
would hand the backlog straight back to the just-frozen sender. We resolve the
healthy pool ourselves (``restriction_status = 'none'`` excludes the frozen
sender) and pick a receiver per row via ``rotation._pick_least_loaded``.

Concurrency safety (mirrors rebalance.py / queue.py worker): the movable-row
claim uses ``status = 'pending'`` + ``FOR UPDATE OF mq SKIP LOCKED`` — rows the
worker already flipped to ``processing`` are excluded by the status guard, and
rows it currently holds are skipped. A second failover call therefore moves 0
(idempotent, FAIL-06).

Session ownership: ``db=None`` (queue.py PEER_FLOOD / ACCOUNT_FROZEN callers) →
the helper opens and commits its OWN session. ``db`` passed (listener antispam
path) → transaction-neutral, the caller commits so pause+flag+failover land in
one atomic commit.

PII discipline (FAIL-08, CLAUDE.md): logs COUNT + sender UUIDs + campaign UUID
ONLY — never ``recipient_phone`` or payloads.
"""

import logging
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.services.rotation import _pick_least_loaded

logger = logging.getLogger(__name__)

# Movable cold-pending predicate (RESEARCH §"never sent / no dialog", D-06
# resolved). Extends rebalance.py:50-64 with:
#   - item_type = 'message'  (D-04.1: only real outreach rows)
#   - the never-sent guard widened to IN ('sent', 'processing') (D-04.2: a
#     mid-send recipient is engaged-in-progress, not cold)
#   - the empty-conversation guard narrowed to a has-message JOIN (D-04.3 / D-05:
#     a conversation with ZERO messages is still cold and movable; only a
#     has-message dialog is engaged). `messages` has no recipient_phone
#     (migration 017) so the join is conversations cv → messages m by
#     conversation_id.
_COLD_PENDING_PREDICATE = """
    mq.status = 'pending'
    AND mq.item_type = 'message'
    AND mq.campaign_id = :cid
    AND NOT EXISTS (
        SELECT 1 FROM message_queue s
        WHERE s.campaign_id = mq.campaign_id
          AND s.recipient_phone = mq.recipient_phone
          AND s.status IN ('sent', 'processing')
    )
    AND NOT EXISTS (
        SELECT 1 FROM conversations cv
        JOIN messages m ON m.conversation_id = cv.id
        WHERE cv.workspace_id = mq.workspace_id
          AND cv.contact_phone = mq.recipient_phone
    )
"""


async def failover_cold_backlog(
    frozen_sender_id: UUID,
    db: AsyncSession | None = None,
) -> int:
    """Move the frozen sender's cold-pending backlog onto healthy pool senders.

    Per-row even spread across the healthy pool (``_pick_least_loaded`` per row),
    keeping ``message_queue`` and the sticky ``campaign_contact_assignments`` in
    lock-step and resetting ``scheduled_at = NOW()`` on each moved row.

    Args:
        frozen_sender_id: the sender that just froze; never chosen as a receiver
            (its ``restriction_status`` is no longer ``'none'``).
        db: when ``None`` the helper opens AND commits its own session
            (queue.py callers); when an ``AsyncSession`` is passed the helper is
            transaction-neutral and the CALLER commits (listener antispam path).

    Returns:
        Total cold-pending rows moved across all the frozen sender's campaigns
        (0 if nothing movable or no healthy receiver — FAIL-07 / D-13).
    """
    if db is None:
        async with AsyncSessionLocal() as own_db:
            moved = await _failover(frozen_sender_id, own_db)
            await own_db.commit()
            return moved
    # Transaction-neutral: caller owns the commit.
    return await _failover(frozen_sender_id, db)


async def _failover(frozen_sender_id: UUID, db: AsyncSession) -> int:
    """Core reassignment over a live session (no commit — caller decides)."""
    frozen_sid = str(frozen_sender_id)

    # Step 1: which campaigns does this frozen sender still have movable backlog in?
    # Group by campaign so the healthy pool is resolved per campaign (the pool +
    # workspace scope close the cross-campaign / cross-workspace leakage surface).
    campaign_rows = (await db.execute(
        text("""
            SELECT DISTINCT campaign_id
            FROM message_queue
            WHERE sender_id = :frozen_sid
              AND status = 'pending'
              AND item_type = 'message'
              AND campaign_id IS NOT NULL
        """),
        {"frozen_sid": frozen_sid},
    )).fetchall()
    campaign_ids = [str(r.campaign_id) for r in campaign_rows]
    if not campaign_ids:
        return 0

    total_moved = 0

    for cid in campaign_ids:
        # Step 2: resolve the healthy pool for this campaign. restriction_status =
        # 'none' excludes the just-frozen sender (Pitfall 1/3). The campaigns JOIN
        # with s.workspace_id = c.workspace_id closes the cross-workspace surface.
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
        candidate_ids = [str(r.sid) for r in pool_rows]

        if len(candidate_ids) < 1:
            # FAIL-07 / D-13: no healthy receiver — leave the rows paused on the
            # frozen sender (reconcile-resume lifts them later). Do NOT touch them.
            logger.info(
                "failover: nowhere to move cold-pending rows off sender %s "
                "in campaign %s (no healthy receiver)",
                frozen_sid, cid,
            )
            continue

        # Step 3: claim the movable cold-pending rows under the worker's own lock
        # discipline (FOR UPDATE OF mq SKIP LOCKED + status='pending') so we never
        # race a row mid-send and a second call moves 0 (FAIL-06).
        claimed = (await db.execute(
            text(f"""
                SELECT mq.id AS id, mq.recipient_phone AS phone
                FROM message_queue mq
                WHERE {_COLD_PENDING_PREDICATE}
                  AND mq.sender_id = :frozen_sid
                FOR UPDATE OF mq SKIP LOCKED
            """),
            {"cid": cid, "frozen_sid": frozen_sid},
        )).fetchall()

        if not claimed:
            continue

        receivers: set[str] = set()
        # Step 4: per-row even spread (D-09) — pick the least-loaded healthy sender
        # for each row, then move queue + sticky CCA in lock-step (rebalance.py:
        # 191-205) and reset scheduled_at = NOW() (EDIT 1 / Pitfall 2: shed the
        # +24h freeze pause so the row is sendable immediately).
        for row in claimed:
            new_sid = str(await _pick_least_loaded(db, candidate_ids))
            await db.execute(
                text("""
                    UPDATE message_queue
                    SET sender_id = :new, scheduled_at = NOW()
                    WHERE id = :rid
                """),
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
            receivers.add(new_sid)

        n = len(claimed)
        total_moved += n
        # FAIL-08: COUNT + source/receiver/campaign UUIDs ONLY — never phones.
        logger.info(
            "failover: moved %d cold-pending rows off sender %s to %d receivers "
            "in campaign %s",
            n, frozen_sid, len(receivers), cid,
        )

    return total_moved
