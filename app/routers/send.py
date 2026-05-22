"""Send router (Phase 3 rewrite — D-06).

POST /api/v1/send — единственный endpoint отправки, под Depends(auth_dep).
Принимает explicit ai_context_id в body. Валидирует workspace-принадлежность агента.

NB: legacy /send-file и /send-batch endpoints удалены в Phase 3 (С-04) — Phase 3
фокус на основном /send. Восстановление batch/file в Phase 4 (CAMP-XX) по необходимости.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import AIContext, Sender
from app.schemas import EnqueueResponse, SendMessageRequest
from app.services.queue import enqueue_message
from app.services.rotation import get_or_assign_sender
from app.utils.auth import AuthCtx, auth_dep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["send"])


@router.post("/send", response_model=EnqueueResponse)
async def send_message(
    request: SendMessageRequest,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Enqueue a Telegram message (workspace-scoped, agent-explicit).

    Body требует: ai_context_id (UUID), recipient_phone (str), message (str).
    Optional: sender_slug (если None — rotation), recipient_name, as_draft, metadata, callback_url.
    """
    # 1. Validate agent exists in caller's workspace (D-06)
    agent_result = await db.execute(
        select(AIContext).where(
            AIContext.id == request.ai_context_id,
            AIContext.workspace_id == ctx.workspace_id,
            # TODO(v2-rls): replaced by RLS policy
        )
    )
    agent = agent_result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "AGENT_NOT_FOUND",
                "message": f"Agent {request.ai_context_id} not found in workspace",
            },
        )

    # 2. Resolve sender — explicit slug OR rotation
    if request.sender_slug:
        sender_result = await db.execute(
            select(Sender).where(
                Sender.slug == request.sender_slug,
                Sender.workspace_id == ctx.workspace_id,
            )
        )
        sender = sender_result.scalar_one_or_none()
        if sender is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "SENDER_NOT_FOUND",
                        "message": f"Sender '{request.sender_slug}' not found"},
            )
        if sender.lifecycle_status != "active" or sender.auth_status != "ok":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "SENDER_NOT_READY",
                    "lifecycle_status": sender.lifecycle_status,
                    "auth_status": sender.auth_status,
                    "message": f"Sender '{request.sender_slug}' is not ready (lifecycle={sender.lifecycle_status}, auth={sender.auth_status})",
                },
            )
    else:
        # Phase 4: rotation now per-campaign — but Phase 3 send.py path
        # is fully rewritten in Plan 04-04 Task 5. This stub fails fast
        # to surface that the caller MUST use campaign_id (D-16).
        raise HTTPException(
            status_code=410,
            detail={
                "code": "ROTATION_REQUIRES_CAMPAIGN_ID",
                "message": "Phase 4: rotation rewrite requires campaign_id (see Plan 04-04 Task 5).",
            },
        )

    # 3. Enqueue with explicit ai_context_id (Plan 03-01 added param)
    try:
        info = await enqueue_message(
            db=db,
            workspace_id=ctx.workspace_id,
            sender_id=sender.id,
            sender_slug=sender.slug,
            recipient_phone=request.recipient_phone,
            recipient_name=request.recipient_name,
            message_text=request.message,
            as_draft=request.as_draft,
            metadata=request.metadata,
            callback_url=request.callback_url,
            ai_context_id=request.ai_context_id,
        )
    except Exception as e:
        logger.error(f"[send] enqueue failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"code": "ENQUEUE_FAILED", "message": str(e)},
        )

    logger.info(
        f"[send] workspace={ctx.workspace_id} agent={request.ai_context_id} "
        f"sender={sender.slug} to={request.recipient_phone} queue={info['queue_id'][:8]}"
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
