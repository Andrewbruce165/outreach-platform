"""
Queue management endpoints.

GET  /api/v1/queue/{queue_id}         — check status of a queued item
GET  /api/v1/queue/stats/{sender_slug} — rate-limit stats for a sender
DELETE /api/v1/queue/{queue_id}       — cancel a pending item
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy import text
from datetime import datetime, timezone, timedelta

from app.database import get_db
from app.models import MessageQueue, QueueItemStatus, Sender
from app.schemas import QueueItemResponse, QueueStatsResponse
from app.routers.auth import verify_api_key
from app.services.queue import MIN_SEND_INTERVAL, MAX_SEND_INTERVAL, _queue_position

router = APIRouter(prefix="/api/v1/queue", tags=["queue"])


@router.get("/stats/{sender_slug}", response_model=QueueStatsResponse)
async def queue_stats(
    sender_slug: str,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    """Return current queue depth and rate-limit counters for a sender."""
    r = await db.execute(select(Sender).where(Sender.slug == sender_slug))
    sender = r.scalar_one_or_none()
    if not sender:
        raise HTTPException(status_code=404, detail=f"Sender '{sender_slug}' not found")

    now = datetime.now(timezone.utc)
    sid = str(sender.id)

    pending = (await db.execute(
        text("SELECT COUNT(*) FROM message_queue WHERE sender_id=:s AND status='pending'"),
        {"s": sid}
    )).scalar()

    processing = (await db.execute(
        text("SELECT COUNT(*) FROM message_queue WHERE sender_id=:s AND status='processing'"),
        {"s": sid}
    )).scalar()

    sent_hour = (await db.execute(
        text("SELECT COUNT(*) FROM message_queue WHERE sender_id=:s AND status='sent' AND finished_at >= :t"),
        {"s": sid, "t": now - timedelta(hours=1)}
    )).scalar()

    sent_minute = (await db.execute(
        text("SELECT COUNT(*) FROM message_queue WHERE sender_id=:s AND status='sent' AND finished_at >= :t"),
        {"s": sid, "t": now - timedelta(minutes=1)}
    )).scalar()

    # ETA for the next pending item
    r2 = await db.execute(
        text("""
            SELECT scheduled_at FROM message_queue
            WHERE sender_id=:s AND status='pending'
            ORDER BY priority DESC, created_at ASC LIMIT 1
        """),
        {"s": sid}
    )
    next_row = r2.fetchone()

    # Factor in minimum interval since last send
    r3 = await db.execute(
        text("SELECT finished_at FROM message_queue WHERE sender_id=:s AND status='sent' ORDER BY finished_at DESC LIMIT 1"),
        {"s": sid}
    )
    last_row = r3.fetchone()
    next_send_at = None
    if last_row and last_row[0]:
        avg_interval = (MIN_SEND_INTERVAL + MAX_SEND_INTERVAL) / 2
        next_send_at = last_row[0] + timedelta(seconds=avg_interval)
    elif next_row and next_row[0]:
        next_send_at = next_row[0]

    return QueueStatsResponse(
        sender_slug=sender_slug,
        pending=pending,
        processing=processing,
        sent_last_hour=sent_hour,
        sent_last_minute=sent_minute,
        next_send_at=next_send_at,
    )


@router.get("/{queue_id}", response_model=QueueItemResponse)
async def get_queue_item(
    queue_id: str,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    """Get the current status of a queued item."""
    r = await db.execute(select(MessageQueue).where(MessageQueue.id == queue_id))
    item: MessageQueue = r.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Queue item not found")

    r2 = await db.execute(select(Sender).where(Sender.id == item.sender_id))
    sender: Sender = r2.scalar_one_or_none()

    position = None
    if item.status == QueueItemStatus.pending:
        position = await _queue_position(db, item.sender_id, item.id)

    return QueueItemResponse(
        id=item.id,
        sender_slug=sender.slug if sender else "unknown",
        item_type=item.item_type.value,
        status=item.status.value,
        recipient_phone=item.recipient_phone,
        recipient_name=item.recipient_name,
        message_text=item.message_text,
        file_url=item.file_url,
        queue_position=position,
        scheduled_at=item.scheduled_at,
        created_at=item.created_at,
        finished_at=item.finished_at,
        result_message_id=item.result_message_id,
        result_recipient_telegram_id=item.result_recipient_telegram_id,
        result_recipient_name=item.result_recipient_name,
        result_recipient_username=item.result_recipient_username,
        error_message=item.error_message,
    )


@router.delete("/{queue_id}")
async def cancel_queue_item(
    queue_id: str,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    """Cancel a pending queue item. Cannot cancel items already processing or sent."""
    r = await db.execute(select(MessageQueue).where(MessageQueue.id == queue_id))
    item: MessageQueue = r.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Queue item not found")

    if item.status != QueueItemStatus.pending:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot cancel item in status '{item.status.value}'"
        )

    item.status = QueueItemStatus.cancelled
    item.finished_at = datetime.now(timezone.utc)
    await db.commit()

    return {"success": True, "queue_id": queue_id, "status": "cancelled"}
