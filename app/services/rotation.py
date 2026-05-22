"""Phase 4 D-06: per-campaign sender rotation.

Provides ``get_or_assign_sender(campaign_id, contact_phone, db)`` — single
entry point for picking a sender for a (campaign, phone) pair.

Replaces the Phase 3 ai-context-keyed variant (Phase 3 D-05) since
migration 016 dropped the legacy assignments table and replaced it with
``campaign_contact_assignments``. Sender pool is now sourced from
``campaign_senders`` through-table — NOT globally from workspace senders.

Race-safety: ``ON CONFLICT (campaign_id, contact_phone) DO NOTHING`` on the
``campaign_contact_assignments`` UNIQUE constraint (migration 016).

Defence-in-depth (Phase 02.1 CR-03 pattern): explicit
``s.workspace_id = c.workspace_id`` guard in SELECT, even though the FK
chain already ensures workspace alignment.

Used by:
- ``app/services/campaign_enqueue.py`` (CampaignEnqueueWorker, commit=False)
- ``app/routers/send.py`` (POST /api/v1/send, default commit=True)
"""

import logging
from typing import Optional
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Sender

logger = logging.getLogger(__name__)


async def get_or_assign_sender(
    campaign_id: UUID,
    contact_phone: str,
    db: AsyncSession,
    *,
    commit: bool = True,
) -> Optional[Sender]:
    """Phase 4 D-06: pick a sender for a (campaign, contact_phone) pair.

    Algorithm:
        1. Look up existing assignment in ``campaign_contact_assignments``.
           If found AND the sender is still eligible — return it.
        2. Resolve campaign's ``workspace_id`` (defence-in-depth).
        3. SELECT active senders from ``campaign_senders`` pool
           (``auth_status='ok' AND lifecycle_status='active'``).
        4. Pick least-loaded sender (least active assignments).
        5. INSERT/UPDATE ``campaign_contact_assignments`` row, race-safe
           via ON CONFLICT DO NOTHING (UNIQUE on campaign_id+phone).
        6. Re-read the winning row (concurrent INSERT may have won).

    Args:
        campaign_id: target campaign UUID.
        contact_phone: E.164 phone (must match contacts.phone normalisation).
        db: ``AsyncSession`` provided by caller.
        commit: if True (default) — function calls ``db.commit()``;
                if False — caller controls commit (used by CampaignEnqueueWorker
                inside ``begin_nested()`` savepoint to avoid double-commit).

    Returns:
        ``Sender`` if assignment succeeded, ``None`` if campaign has no active
        senders (caller decides whether to skip or 409).

    # TODO(v2-rls): replaced by RLS policy app.workspace_id.
    """
    cid_str = str(campaign_id)

    # Step 1: existing assignment + sender eligibility (one query).
    row = (await db.execute(
        text("""
            SELECT cca.sender_id,
                   c.workspace_id AS workspace_id,
                   (s.lifecycle_status = 'active' AND s.auth_status = 'ok') AS is_eligible
            FROM campaign_contact_assignments cca
            JOIN campaigns c ON c.id = cca.campaign_id
            JOIN senders s ON s.id = cca.sender_id
            WHERE cca.campaign_id = :cid
              AND cca.contact_phone = :phone
        """),
        {"cid": cid_str, "phone": contact_phone},
    )).fetchone()

    existing_sender_id = None
    workspace_id_str: Optional[str] = None
    if row is not None:
        existing_sender_id = row.sender_id
        workspace_id_str = str(row.workspace_id)
        if row.is_eligible:
            # Happy path — return as-is.
            res = await db.execute(select(Sender).where(Sender.id == existing_sender_id))
            sender = res.scalar_one_or_none()
            if sender is not None:
                return sender
        # else: assignment exists but sender went offline → reassign below.

    # Step 2: resolve workspace_id if we didn't get it from step 1.
    if workspace_id_str is None:
        camp_row = (await db.execute(
            text("SELECT workspace_id FROM campaigns WHERE id = :cid"),
            {"cid": cid_str},
        )).fetchone()
        if camp_row is None:
            logger.warning("Rotation: campaign %s does not exist", cid_str)
            return None
        workspace_id_str = str(camp_row.workspace_id)

    # Step 3: SELECT active senders from campaign_senders pool.
    # Phase 02.1 CR-03: explicit s.workspace_id guard.
    candidates_rows = await db.execute(
        text("""
            SELECT s.id AS sid
            FROM campaign_senders cs
            JOIN senders s ON s.id = cs.sender_id
            WHERE cs.campaign_id = :cid
              AND s.lifecycle_status = 'active'
              AND s.auth_status = 'ok'
              AND s.role = 'sender'
              AND s.workspace_id = :wid
        """),
        {"cid": cid_str, "wid": workspace_id_str},
    )
    candidates = [r.sid for r in candidates_rows.fetchall()]
    if not candidates:
        logger.warning(
            "Rotation: no active senders in campaign %s (workspace %s)",
            cid_str, workspace_id_str,
        )
        return None

    # Step 4: pick least-loaded sender among candidates (load balancing).
    picked_id = await _pick_least_loaded(db, candidates)

    # Step 5: INSERT or UPDATE the assignment.
    if existing_sender_id is not None:
        # Stale assignment (sender went offline) — UPDATE to new sender.
        await db.execute(
            text("""
                UPDATE campaign_contact_assignments
                SET sender_id = :sid
                WHERE campaign_id = :cid AND contact_phone = :phone
            """),
            {"sid": str(picked_id), "cid": cid_str, "phone": contact_phone},
        )
    else:
        # Fresh INSERT, race-safe via ON CONFLICT.
        await db.execute(
            text("""
                INSERT INTO campaign_contact_assignments
                    (workspace_id, campaign_id, contact_phone, sender_id)
                VALUES (:wid, :cid, :phone, :sid)
                ON CONFLICT (campaign_id, contact_phone) DO NOTHING
            """),
            {
                "wid": workspace_id_str,
                "cid": cid_str,
                "phone": contact_phone,
                "sid": str(picked_id),
            },
        )

    if commit:
        # Direct caller (send.py, ai_engine) — commit here.
        # CampaignEnqueueWorker passes commit=False because it wraps the call
        # in begin_nested() savepoint and commits the outer transaction itself.
        await db.commit()

    # Step 6: re-read the winning row (concurrent INSERT may have won).
    winner_row = (await db.execute(
        text("""
            SELECT sender_id FROM campaign_contact_assignments
            WHERE campaign_id = :cid AND contact_phone = :phone
        """),
        {"cid": cid_str, "phone": contact_phone},
    )).fetchone()

    if winner_row is None:
        # Shouldn't happen — we just inserted. Defensive.
        logger.error(
            "Rotation: assignment vanished after INSERT for campaign %s phone %s",
            cid_str, contact_phone,
        )
        return None

    winner_id = winner_row.sender_id
    sender = (await db.execute(
        select(Sender).where(Sender.id == winner_id)
    )).scalar_one_or_none()
    if sender is None:
        logger.warning("Rotation: sender %s not found post-assignment", winner_id)
        return None
    return sender


async def _pick_least_loaded(db: AsyncSession, sender_ids: list) -> UUID:
    """Pick the sender_id with the fewest active campaign_contact_assignments.

    Used for load-balancing within a campaign's sender pool. Tie-break is
    arbitrary (first row in result set).
    """
    rows = await db.execute(
        text("""
            SELECT s.id AS sid, COUNT(cca.id) AS cnt
            FROM senders s
            LEFT JOIN campaign_contact_assignments cca ON cca.sender_id = s.id
            WHERE s.id = ANY(:ids)
            GROUP BY s.id
            ORDER BY cnt ASC, s.created_at ASC
            LIMIT 1
        """),
        {"ids": [str(i) for i in sender_ids]},
    )
    r = rows.fetchone()
    return r.sid if r else sender_ids[0]
