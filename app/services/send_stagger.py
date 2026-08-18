"""B4 — start/resume burst desync: per-sender stagger of the FIRST cold dialog.

Why: the queue times sends only WITHIN a sender (base interval 20-55s, fatigue,
pace jitter, per-sender 4/min-20/h-150/day). Nothing phase-shifts the FIRST send
BETWEEN senders, and on start/resume every attached sender's last_used_at is far
in the past, so the whole pool becomes due in the same tick and opens cold
dialogs within seconds of each other — a confirmed cluster signature (mass-ban
campaign 24658b65).

This module writes ONLY senders.send_stagger_until. It never touches
restriction_status / lifecycle_status / restricted_until, writes no
sender_restriction_events row, and does not change any rate limit or interval.
The send worker treats an unexpired marker as "skip this sender for NEW dialogs";
follow-ups are unaffected.

TRANSACTION-NEUTRAL: does NOT commit — the caller owns the transaction (same
contract as services/rebalance.py).
"""

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings

logger = logging.getLogger(__name__)

# Eligible pool — filter copied verbatim from rebalance.py:353-367 /
# rotation.py:113-131 (keep in sync).
_ELIGIBLE_POOL_SQL = """
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
"""


async def apply_send_stagger(db: AsyncSession, campaign_id) -> int:
    """Lay out send_stagger_until across the campaign's attached ELIGIBLE senders.

    Layout (D-3): offset_i = i * (W/N) + uniform(0, W/N) over a random
    permutation of the pool — disjoint half-open slots, so two senders can never
    collide and max offset < W. Returns the number of senders staggered.

    W == 0 (kill switch) or N < 2 (nothing to desync) → clear the pool's markers
    to NULL and return 0.
    """
    cid = str(campaign_id)
    w = get_settings().send_stagger_window_seconds

    attached_rows = (await db.execute(
        text("SELECT sender_id AS sid FROM campaign_senders WHERE campaign_id = :cid"),
        {"cid": cid},
    )).fetchall()
    attached_ids = [str(r.sid) for r in attached_rows]
    if not attached_ids:
        return 0

    pool_rows = (await db.execute(text(_ELIGIBLE_POOL_SQL), {"cid": cid})).fetchall()
    pool_ids = [str(r.sid) for r in pool_rows]
    n = len(pool_ids)

    if w <= 0 or n < 2:
        # Kill switch (W=0, D-1) or nothing to desync (N<2, D-6 — mirrors
        # rebalance_campaign_even's P<2 early return). Clear EVERY attached
        # sender's marker, not just the eligible ones: a sender that has since
        # become ineligible must not carry a stale future timestamp.
        await db.execute(
            text("UPDATE senders SET send_stagger_until = NULL WHERE id = ANY(:ids)"),
            {"ids": attached_ids},
        )
        logger.info(
            "send stagger: no-op for campaign %s (window=%ds, eligible=%d) — markers cleared",
            cid, w, n,
        )
        return 0

    # One set-based UPDATE. row_number() over a random permutation assigns each
    # sender its own slot i; random() returns [0,1) so slot i stays inside
    # [i*W/N, (i+1)*W/N) — distinctness (up to timestamp resolution) and the < W
    # upper bound are STRUCTURAL, not probabilistic.
    await db.execute(
        text("""
            WITH pool AS (
                SELECT s.id AS sid,
                       row_number() OVER (ORDER BY random()) - 1 AS i,
                       count(*) OVER () AS n
                FROM campaign_senders cs
                JOIN senders s ON s.id = cs.sender_id
                JOIN campaigns c ON c.id = cs.campaign_id
                WHERE cs.campaign_id = :cid
                  AND s.lifecycle_status = 'active'
                  AND s.auth_status = 'ok'
                  AND s.role = 'sender'
                  AND s.restriction_status = 'none'
                  AND s.workspace_id = c.workspace_id
            )
            UPDATE senders s
               SET send_stagger_until = NOW() + make_interval(secs =>
                     (pool.i * (CAST(:w AS DOUBLE PRECISION) / pool.n))
                     + (random() * (CAST(:w AS DOUBLE PRECISION) / pool.n)))
              FROM pool
             WHERE s.id = pool.sid
        """),
        {"cid": cid, "w": w},
    )

    # Attached-but-ineligible senders must not keep a stale marker from an earlier
    # start/resume — otherwise they would stay blocked for new dialogs after
    # recovering. Set-based, same transaction.
    await db.execute(
        text("""
            UPDATE senders SET send_stagger_until = NULL
             WHERE id = ANY(:attached_ids)
               AND id <> ALL(:pool_ids)
               AND send_stagger_until IS NOT NULL
        """),
        {"attached_ids": attached_ids, "pool_ids": pool_ids},
    )

    logger.info(
        "send stagger: laid out %d senders over %ds for campaign %s", n, w, campaign_id
    )
    return n
