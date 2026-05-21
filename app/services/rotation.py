"""
Sender rotation service.

Provides get_or_assign_sender() — the single entry point for context-based
sender selection. Persists (context_id, contact_phone) → sender_id in
context_contact_assignments table and reuses the assignment on subsequent calls.

Can be called from the HTTP router and from the listener process.
"""

import logging
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Sender

logger = logging.getLogger(__name__)


async def get_or_assign_sender(
    db: AsyncSession,
    context_id: UUID,
    contact_phone: str,
    workspace_id: UUID,
) -> Sender:
    """
    Return the sender assigned to (workspace_id, context_id, contact_phone),
    creating the assignment if it doesn't exist yet.

    Phase 02.1 (CR-03): workspace_id is now a required parameter. All SELECT,
    INSERT and UPDATE statements in this function carry an explicit workspace
    guard, defence-in-depth against AIContext/Sender workspace_id divergence.

    Algorithm:
    1. Look up existing assignment in context_contact_assignments scoped to workspace.
    2. If found and sender is eligible — return it.
    3. If found but sender is not eligible — pick a new one (within workspace),
       update the record.
    4. If not found — pick the most free eligible sender, INSERT with
       ON CONFLICT DO NOTHING, then re-read the winner.
    5. If no eligible senders linked to the context within this workspace —
       raise ValueError.

    "Most free" = fewest messages with status='sent' in the last 24 hours.
    Tie-break: oldest sender by created_at (longest idle).
    """
    ctx_str = str(context_id)
    wid_str = str(workspace_id)

    # Step 1: check existing assignment (scoped to workspace).
    # Phase 2 (D-11/D-12): senders.is_active dropped → "eligible" = lifecycle_status='active' AND auth_status='ok'.
    # Phase 02.1 (CR-03): WHERE cca.workspace_id = :wid — defence-in-depth.
    row = (await db.execute(
        text("""
            SELECT cca.sender_id,
                   (s.lifecycle_status = 'active' AND s.auth_status = 'ok') AS is_eligible
            FROM context_contact_assignments cca
            JOIN senders s ON s.id = cca.sender_id
            WHERE cca.context_id = :ctx_id
              AND cca.contact_phone = :phone
              AND cca.workspace_id = :wid
        """),
        {"ctx_id": ctx_str, "phone": contact_phone, "wid": wid_str},
    )).fetchone()

    if row is not None:
        assigned_sender_id, is_eligible = row[0], row[1]

        if is_eligible:
            # Happy path: existing assignment, sender still eligible
            result = await db.execute(select(Sender).where(Sender.id == assigned_sender_id))
            return result.scalar_one()

        # Assigned sender went inactive — pick a replacement within workspace
        logger.info(
            "Rotation: sender %s for contact %s (context %s) is not eligible, reassigning",
            assigned_sender_id, contact_phone, ctx_str[:8],
        )
        new_sender = await _pick_best_sender(db, context_id, workspace_id)
        if new_sender is None:
            raise ValueError(
                f"No active senders linked to context {context_id} in workspace {workspace_id}"
            )

        await db.execute(
            text("""
                UPDATE context_contact_assignments
                SET sender_id = :new_sid, updated_at = NOW()
                WHERE context_id = :ctx_id
                  AND contact_phone = :phone
                  AND workspace_id = :wid
            """),
            {
                "new_sid": str(new_sender.id),
                "ctx_id": ctx_str,
                "phone": contact_phone,
                "wid": wid_str,
            },
        )
        await db.commit()
        logger.info(
            "Rotation: reassigned %s in context %s to %s",
            contact_phone, ctx_str[:8], new_sender.slug,
        )
        return new_sender

    # Step 4: no assignment yet — pick best and insert
    best = await _pick_best_sender(db, context_id, workspace_id)
    if best is None:
        raise ValueError(
            f"No active senders linked to context {context_id} in workspace {workspace_id}"
        )

    # Phase 02.1 (CR-03): context_contact_assignments.workspace_id NOT NULL
    # after migration 012. ON CONFLICT key remains (context_id, contact_phone)
    # per migration 007 uniqueness — adding workspace_id here would not change
    # the conflict key (one context belongs to one workspace by design).
    await db.execute(
        text("""
            INSERT INTO context_contact_assignments (workspace_id, context_id, contact_phone, sender_id)
            VALUES (:wid, :ctx_id, :phone, :sid)
            ON CONFLICT (context_id, contact_phone) DO NOTHING
        """),
        {
            "wid": wid_str,
            "ctx_id": ctx_str,
            "phone": contact_phone,
            "sid": str(best.id),
        },
    )
    await db.commit()

    # Re-read: a concurrent request may have won the INSERT race
    winner_id = (await db.execute(
        text("""
            SELECT sender_id FROM context_contact_assignments
            WHERE context_id = :ctx_id
              AND contact_phone = :phone
              AND workspace_id = :wid
        """),
        {"ctx_id": ctx_str, "phone": contact_phone, "wid": wid_str},
    )).scalar_one()

    if winner_id == best.id:
        logger.info(
            "Rotation: assigned %s in context %s to %s",
            contact_phone, ctx_str[:8], best.slug,
        )
        return best

    # Another request won — respect their choice and return the winner
    result = await db.execute(select(Sender).where(Sender.id == winner_id))
    winner = result.scalar_one()
    logger.info(
        "Rotation: concurrent assignment for %s in context %s resolved to %s",
        contact_phone, ctx_str[:8], winner.slug,
    )
    return winner


async def _pick_best_sender(
    db: AsyncSession,
    context_id: UUID,
    workspace_id: UUID,
) -> Sender | None:
    """
    Return the active sender within `workspace_id` with the fewest messages
    sent in the last 24 hours. Returns None if no active senders exist.

    Phase 3 D-04: sender больше не «знает» агента (senders.ai_context_id dropped) —
    выбор идёт по всему workspace pool. `context_id` параметр сохраняется в
    сигнатуре для обратной совместимости с get_or_assign_sender (который
    продолжает писать context_contact_assignments per D-05) — но в SQL filter
    больше не используется.

    Phase 02.1 (CR-03): workspace_id guard сохранён — defence-in-depth.

    TODO(phase-4): selection по campaign_id когда появится Campaign.sender_lock.

    Single SQL query — no N+1. Tie-break: oldest sender (longest idle).
    Only senders with role='sender' are considered (excludes checkers).
    """
    # Phase 2 (D-11/D-12): senders.is_active dropped → lifecycle_status + auth_status.
    # Phase 3 D-04: больше нет фильтра по s.ai_context_id — workspace-only выбор.
    row = (await db.execute(
        text("""
            SELECT s.id
            FROM senders s
            LEFT JOIN message_queue mq
                ON mq.sender_id = s.id
                AND mq.status = 'sent'
                AND mq.finished_at >= NOW() - INTERVAL '24 hours'
            WHERE s.workspace_id = :wid
              AND s.lifecycle_status = 'active'
              AND s.auth_status = 'ok'
              AND s.role = 'sender'
            GROUP BY s.id, s.created_at
            ORDER BY COUNT(mq.id) ASC, s.created_at ASC
            LIMIT 1
        """),
        {"wid": str(workspace_id)},
    )).fetchone()

    if row is None:
        return None

    result = await db.execute(select(Sender).where(Sender.id == row[0]))
    return result.scalar_one_or_none()
