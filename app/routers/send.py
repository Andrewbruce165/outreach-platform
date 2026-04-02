from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy import text
from datetime import datetime, timezone
from app.database import get_db
from app.models import Sender, AIContext
from app.schemas import (
    SendMessageRequest, SendFileRequest, EnqueueResponse,
    BatchSendRequest, BatchSendResponse, BatchEnqueueResult,
)
from app.services.queue import enqueue_message, enqueue_file
from app.services.rotation import get_or_assign_sender
from app.routers.auth import verify_api_key
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["send"])


@router.post("/send", response_model=EnqueueResponse)
async def send_message(
    request: SendMessageRequest,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key)
):
    """
    Enqueue a Telegram message for rate-limited delivery.

    Returns immediately with queue position and estimated send time.
    Use GET /api/v1/queue/{queue_id} to poll for the result.
    """

    # Resolve sender: explicit slug OR auto-rotation by context
    if request.sender:
        result = await db.execute(
            select(Sender).where(Sender.slug == request.sender)
        )
        sender = result.scalar_one_or_none()

        if not sender:
            return EnqueueResponse(
                success=False,
                queued=False,
                timestamp=datetime.now(timezone.utc),
                error={
                    "code": "SENDER_NOT_FOUND",
                    "message": f"Отправитель '{request.sender}' не найден"
                }
            )

        if not sender.is_active:
            return EnqueueResponse(
                success=False,
                queued=False,
                timestamp=datetime.now(timezone.utc),
                error={
                    "code": "SENDER_INACTIVE",
                    "message": f"Аккаунт отправителя '{request.sender}' деактивирован"
                }
            )
    else:
        # Auto-select sender via context rotation
        ctx_result = await db.execute(
            select(AIContext).where(
                AIContext.id == request.ai_context_id,
                AIContext.is_active == True,
            )
        )
        if ctx_result.scalar_one_or_none() is None:
            return EnqueueResponse(
                success=False,
                queued=False,
                timestamp=datetime.now(timezone.utc),
                error={
                    "code": "CONTEXT_NOT_FOUND",
                    "message": f"AI-контекст '{request.ai_context_id}' не найден или неактивен"
                }
            )

        try:
            sender = await get_or_assign_sender(
                db=db,
                context_id=request.ai_context_id,
                contact_phone=request.recipient_phone,
            )
        except ValueError as e:
            return EnqueueResponse(
                success=False,
                queued=False,
                timestamp=datetime.now(timezone.utc),
                error={
                    "code": "NO_ACTIVE_SENDER",
                    "message": str(e)
                }
            )

    try:
        info = await enqueue_message(
            db=db,
            sender_id=sender.id,
            sender_slug=sender.slug,
            recipient_phone=request.recipient_phone,
            recipient_name=request.recipient_name,
            message_text=request.message,
            as_draft=request.as_draft,
            metadata=request.metadata,
            callback_url=request.callback_url,
        )
        logger.info(
            f"Queued message for {request.recipient_phone} via {sender.slug} "
            f"(pos={info['queue_position']}, id={info['queue_id'][:8]})"
        )
        return EnqueueResponse(
            success=True,
            queued=True,
            queue_id=info["queue_id"],
            queue_position=info["queue_position"],
            sender_slug=sender.slug,
            estimated_send_at=info["estimated_send_at"],
            timestamp=datetime.now(timezone.utc),
        )
    except Exception as e:
        logger.error(f"Failed to enqueue message: {e}", exc_info=True)
        return EnqueueResponse(
            success=False,
            queued=False,
            timestamp=datetime.now(timezone.utc),
            error={
                "code": "ENQUEUE_FAILED",
                "message": str(e)
            }
        )


@router.post("/send-file", response_model=EnqueueResponse)
async def send_file(
    request: SendFileRequest,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key)
):
    """
    Enqueue a file send for rate-limited delivery.

    Returns immediately with queue position and estimated send time.
    Use GET /api/v1/queue/{queue_id} to poll for the result.
    """

    # Resolve sender: explicit slug OR auto-rotation by context
    if request.sender:
        result = await db.execute(
            select(Sender).where(Sender.slug == request.sender)
        )
        sender = result.scalar_one_or_none()

        if not sender:
            return EnqueueResponse(
                success=False,
                queued=False,
                timestamp=datetime.now(timezone.utc),
                error={
                    "code": "SENDER_NOT_FOUND",
                    "message": f"Отправитель '{request.sender}' не найден"
                }
            )

        if not sender.is_active:
            return EnqueueResponse(
                success=False,
                queued=False,
                timestamp=datetime.now(timezone.utc),
                error={
                    "code": "SENDER_INACTIVE",
                    "message": f"Аккаунт отправителя '{request.sender}' деактивирован"
                }
            )
    else:
        # Auto-select sender via context rotation
        ctx_result = await db.execute(
            select(AIContext).where(
                AIContext.id == request.ai_context_id,
                AIContext.is_active == True,
            )
        )
        if ctx_result.scalar_one_or_none() is None:
            return EnqueueResponse(
                success=False,
                queued=False,
                timestamp=datetime.now(timezone.utc),
                error={
                    "code": "CONTEXT_NOT_FOUND",
                    "message": f"AI-контекст '{request.ai_context_id}' не найден или неактивен"
                }
            )

        try:
            sender = await get_or_assign_sender(
                db=db,
                context_id=request.ai_context_id,
                contact_phone=request.recipient_phone,
            )
        except ValueError as e:
            return EnqueueResponse(
                success=False,
                queued=False,
                timestamp=datetime.now(timezone.utc),
                error={
                    "code": "NO_ACTIVE_SENDER",
                    "message": str(e)
                }
            )

    try:
        info = await enqueue_file(
            db=db,
            sender_id=sender.id,
            sender_slug=sender.slug,
            recipient_phone=request.recipient_phone,
            recipient_name=request.recipient_name,
            file_url=request.file_url,
            file_name=request.file_name,
            caption=request.caption,
            metadata=request.metadata,
            callback_url=request.callback_url,
        )
        logger.info(
            f"Queued file for {request.recipient_phone} via {sender.slug} "
            f"(pos={info['queue_position']}, id={info['queue_id'][:8]})"
        )
        return EnqueueResponse(
            success=True,
            queued=True,
            queue_id=info["queue_id"],
            queue_position=info["queue_position"],
            sender_slug=sender.slug,
            estimated_send_at=info["estimated_send_at"],
            timestamp=datetime.now(timezone.utc),
        )
    except Exception as e:
        logger.error(f"Failed to enqueue file: {e}", exc_info=True)
        return EnqueueResponse(
            success=False,
            queued=False,
            timestamp=datetime.now(timezone.utc),
            error={
                "code": "ENQUEUE_FAILED",
                "message": str(e)
            }
        )


MAX_QUEUE_PER_SENDER = 1000


@router.post("/send-batch", response_model=BatchSendResponse)
async def send_batch(
    request: BatchSendRequest,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    """
    Enqueue multiple recipients in a single request (up to 500).

    Returns per-recipient queue IDs. Use GET /api/v1/queue/{queue_id}
    to poll individual items.
    """
    result = await db.execute(select(Sender).where(Sender.slug == request.sender))
    sender = result.scalar_one_or_none()

    if not sender:
        raise HTTPException(status_code=404, detail={
            "code": "SENDER_NOT_FOUND",
            "message": f"Отправитель '{request.sender}' не найден",
        })

    if not sender.is_active:
        raise HTTPException(status_code=409, detail={
            "code": "SENDER_INACTIVE",
            "message": f"Аккаунт отправителя '{request.sender}' деактивирован",
        })

    pending_count = (await db.execute(
        text("SELECT COUNT(*) FROM message_queue WHERE sender_id=:s AND status='pending'"),
        {"s": str(sender.id)},
    )).scalar()

    if pending_count + len(request.recipients) > MAX_QUEUE_PER_SENDER:
        raise HTTPException(status_code=400, detail={
            "code": "QUEUE_OVERFLOW",
            "message": (
                f"Queue overflow: {pending_count} pending + "
                f"{len(request.recipients)} new > {MAX_QUEUE_PER_SENDER}"
            ),
        })

    results: list[BatchEnqueueResult] = []
    queued = 0
    for recipient in request.recipients:
        try:
            info = await enqueue_message(
                db=db,
                sender_id=sender.id,
                sender_slug=sender.slug,
                recipient_phone=recipient.phone,
                recipient_name=recipient.name,
                message_text=request.message,
                as_draft=request.as_draft,
                metadata=recipient.metadata,
                priority=request.priority,
                callback_url=request.callback_url,
            )
            results.append(BatchEnqueueResult(
                phone=recipient.phone,
                queued=True,
                queue_id=info["queue_id"],
            ))
            queued += 1
        except Exception as e:
            results.append(BatchEnqueueResult(
                phone=recipient.phone,
                queued=False,
                error=str(e),
            ))

    logger.info(
        f"Batch queued {queued}/{len(request.recipients)} messages via {sender.slug}"
    )
    return BatchSendResponse(
        total=len(request.recipients),
        queued=queued,
        failed=len(request.recipients) - queued,
        results=results,
        timestamp=datetime.now(timezone.utc),
    )
