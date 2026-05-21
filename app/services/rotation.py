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
) -> Sender:
    """
    Return the sender assigned to (context_id, contact_phone), creating the
    assignment if it doesn't exist yet.

    Algorithm:
    1. Look up existing assignment in context_contact_assignments.
    2. If found and sender is active — return it.
    3. If found but sender is inactive — pick a new one, update the record.
    4. If not found — pick the most free active sender, INSERT with
       ON CONFLICT DO NOTHING to guard against race conditions, then re-read
       the winner (another concurrent request may have won the INSERT race).
    5. If no active senders are linked to the context — raise ValueError.

    "Most free" = fewest messages with status='sent' in the last 24 hours.
    Tie-break: oldest sender by created_at (longest idle).
    """
    ctx_str = str(context_id)

    # Step 1: check existing assignment
    # Phase 2 (D-11/D-12): senders.is_active dropped → "eligible" = lifecycle_status='active' AND auth_status='ok'.
    row = (await db.execute(
        text("""
            SELECT cca.sender_id,
                   (s.lifecycle_status = 'active' AND s.auth_status = 'ok') AS is_eligible
            FROM context_contact_assignments cca
            JOIN senders s ON s.id = cca.sender_id
            WHERE cca.context_id = :ctx_id
              AND cca.contact_phone = :phone
        """),
        {"ctx_id": ctx_str, "phone": contact_phone},
    )).fetchone()

    if row is not None:
        assigned_sender_id, is_eligible = row[0], row[1]

        if is_eligible:
            # Happy path: existing assignment, sender still eligible
            result = await db.execute(select(Sender).where(Sender.id == assigned_sender_id))
            return result.scalar_one()

        # Assigned sender went inactive — pick a replacement
        logger.info(
            "Rotation: sender %s for contact %s (context %s) is not eligible, reassigning",
            assigned_sender_id, contact_phone, ctx_str[:8],
        )
        new_sender = await _pick_best_sender(db, context_id)
        if new_sender is None:
            raise ValueError(f"No active senders linked to context {context_id}")

        await db.execute(
            text("""
                UPDATE context_contact_assignments
                SET sender_id = :new_sid, updated_at = NOW()
                WHERE context_id = :ctx_id AND contact_phone = :phone
            """),
            {"new_sid": str(new_sender.id), "ctx_id": ctx_str, "phone": contact_phone},
        )
        await db.commit()
        logger.info(
            "Rotation: reassigned %s in context %s to %s",
            contact_phone, ctx_str[:8], new_sender.slug,
        )
        return new_sender

    # Step 4: no assignment yet — pick best and insert
    best = await _pick_best_sender(db, context_id)
    if best is None:
        raise ValueError(f"No active senders linked to context {context_id}")

    await db.execute(
        text("""
            INSERT INTO context_contact_assignments (context_id, contact_phone, sender_id)
            VALUES (:ctx_id, :phone, :sid)
            ON CONFLICT (context_id, contact_phone) DO NOTHING
        """),
        {"ctx_id": ctx_str, "phone": contact_phone, "sid": str(best.id)},
    )
    await db.commit()

    # Re-read: a concurrent request may have won the INSERT race
    winner_id = (await db.execute(
        text("""
            SELECT sender_id FROM context_contact_assignments
            WHERE context_id = :ctx_id AND contact_phone = :phone
        """),
        {"ctx_id": ctx_str, "phone": contact_phone},
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


async def _pick_best_sender(db: AsyncSession, context_id: UUID) -> Sender | None:
    """
    Return the active sender linked to context_id with the fewest messages
    sent in the last 24 hours. Returns None if no active senders exist.

    Single SQL query — no N+1. Tie-break: oldest sender (longest idle).
    Only senders with role='sender' are considered (excludes checkers).
    """
    # Phase 2 (D-11/D-12): senders.is_active dropped → lifecycle_status + auth_status.
    row = (await db.execute(
        text("""
            SELECT s.id
            FROM senders s
            LEFT JOIN message_queue mq
                ON mq.sender_id = s.id
                AND mq.status = 'sent'
                AND mq.finished_at >= NOW() - INTERVAL '24 hours'
            WHERE s.ai_context_id = :ctx_id
              AND s.lifecycle_status = 'active'
              AND s.auth_status = 'ok'
              AND s.role = 'sender'
            GROUP BY s.id, s.created_at
            ORDER BY COUNT(mq.id) ASC, s.created_at ASC
            LIMIT 1
        """),
        {"ctx_id": str(context_id)},
    )).fetchone()

    if row is None:
        return None

    result = await db.execute(select(Sender).where(Sender.id == row[0]))
    return result.scalar_one_or_none()
