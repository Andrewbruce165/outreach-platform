"""Send router (Phase 4 D-16 rewrite — campaign-centric POST /send).

POST /api/v1/send — единственный endpoint отправки, под Depends(auth_dep).
Принимает explicit ``campaign_id`` в body. Agent выводится через JOIN на
campaigns. Workspace API-key push (n8n) продолжает работать тем же endpoint'ом.

D-16:
- Legacy Phase 3 body param replaced with ``campaign_id``.
- ``sender_slug`` опционален: если None — rotation.get_or_assign_sender per-campaign.
- ``message`` опционален: если None — render_template(campaign.message_template, contact).

NB: legacy ``/send-file`` and ``/send-batch`` endpoints удалены в Phase 3
(С-04). File-flow остаётся через ``enqueue_file`` (queue.py) — see Task 5
B1 revision (signature принимает ``campaign_id``).
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Campaign, Sender
from app.schemas import EnqueueResponse, SendMessageRequest
from app.services.queue import enqueue_message
from app.services.rotation import get_or_assign_sender
from app.services.template import render_template
from app.utils.auth import AuthCtx, auth_dep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["send"])


async def _lookup_contact_dict(
    db: AsyncSession,
    workspace_id,
    phone: str,
    fallback_name: Optional[str],
) -> dict:
    """Lookup contact by (workspace_id, phone). Returns dict for render_template."""
    row = (await db.execute(
        text("""
            SELECT phone, full_name, username, source, custom
            FROM contacts
            WHERE phone = :phone AND workspace_id = :wid
            LIMIT 1
        """),
        {"phone": phone, "wid": str(workspace_id)},
    )).fetchone()
    if row is None:
        return {
            "phone": phone,
            "full_name": fallback_name or "",
            "username": None,
            "source": None,
            "custom": {},
        }
    return {
        "phone": row.phone,
        "full_name": row.full_name or (fallback_name or ""),
        "username": row.username,
        "source": row.source,
        "custom": row.custom or {},
    }


@router.post("/send", response_model=EnqueueResponse)
async def send_message(
    request: SendMessageRequest,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Enqueue a Telegram message (workspace-scoped, campaign-explicit).

    Body требует: campaign_id (UUID), recipient_phone (str).
    Optional: sender_slug (если None — rotation), recipient_name, message (если
    None — render_template), as_draft, metadata, callback_url.
    """
    # 1. Validate campaign exists in caller's workspace (D-16).
    # TODO(v2-rls): replaced by RLS policy.
    campaign_result = await db.execute(
        select(Campaign).where(
            Campaign.id == request.campaign_id,
            Campaign.workspace_id == ctx.workspace_id,
        )
    )
    campaign = campaign_result.scalar_one_or_none()
    if campaign is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "CAMPAIGN_NOT_FOUND",
                "message": f"Campaign {request.campaign_id} not found in workspace",
            },
        )

    # 2. Resolve sender — explicit slug OR per-campaign rotation.
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
                    "message": (
                        f"Sender '{request.sender_slug}' is not ready "
                        f"(lifecycle={sender.lifecycle_status}, auth={sender.auth_status})"
                    ),
                },
            )
    else:
        # Phase 4 D-06 rotation — per-campaign pool, NOT workspace-wide.
        sender = await get_or_assign_sender(
            campaign.id, request.recipient_phone, db,
        )
        if sender is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "NO_ACTIVE_SENDER_IN_CAMPAIGN",
                    "message": (
                        f"Campaign {request.campaign_id} has no active senders "
                        f"(check campaign_senders attachment + sender auth_status)."
                    ),
                },
            )

    # 3. Resolve message text — explicit OR render_template from campaign.
    if request.message:
        message_text = request.message
    else:
        contact_dict = await _lookup_contact_dict(
            db, ctx.workspace_id, request.recipient_phone, request.recipient_name,
        )
        message_text = render_template(
            campaign.message_template,
            contact_dict,
            campaign_id=str(campaign.id),
            phone=request.recipient_phone,
        )
        if not message_text:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "EMPTY_MESSAGE",
                    "message": "Rendered message is empty (campaign.message_template is empty?).",
                },
            )

    # 4. Enqueue with explicit campaign_id (D-16) — agent_id derived via JOIN at conversation creation.
    try:
        info = await enqueue_message(
            db=db,
            workspace_id=ctx.workspace_id,
            sender_id=sender.id,
            sender_slug=sender.slug,
            recipient_phone=request.recipient_phone,
            recipient_name=request.recipient_name,
            message_text=message_text,
            as_draft=request.as_draft,
            metadata=request.metadata,
            callback_url=request.callback_url,
            campaign_id=campaign.id,
        )
    except Exception as e:
        logger.error(f"[send] enqueue failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"code": "ENQUEUE_FAILED", "message": str(e)},
        )

    logger.info(
        f"[send] workspace={ctx.workspace_id} campaign={request.campaign_id} "
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
