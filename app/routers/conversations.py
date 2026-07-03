"""Conversations / Inbox router — Phase 5 rewrite (INBX-01..05, AIRC-04).

Workspace-scoped CRUD inbox с filters + manual manager mode (D-01..D-04).
Заменяет legacy router (использовал устаревший верификатор API-ключа и
выпиленную колонку senders, был НЕ зарегистрирован в main.py с Phase 1).

Endpoints (all under Depends(auth_dep) + workspace-scope):
    GET    /api/v1/conversations                       — list with filters (INBX-01, INBX-05)
    GET    /api/v1/conversations/{id}                  — single (INBX-02)
    GET    /api/v1/conversations/{id}/messages         — history with pagination (INBX-02)
    PATCH  /api/v1/conversations/{id}                  — update ai_enabled/status/agent (INBX-03)
    POST   /api/v1/conversations/{id}/disable-ai       — manual takeover (INBX-04, D-01/D-02)
    POST   /api/v1/conversations/{id}/enable-ai        — AI back on (INBX-04, D-03)
    POST   /api/v1/conversations/{id}/send             — manager sends UI (INBX-04, D-04)
    POST   /api/v1/conversations/delete                — bulk hard delete (CASCADE)
    DELETE /api/v1/conversations/{id}                  — hard delete (CASCADE)
    GET    /api/v1/conversations/{id}/llm-calls        — LLM audit log (ANLX-05)

D-17: list endpoint hides status='bot_ignored' by default; explicit
?status=bot_ignored returns them.

D-03: enable-ai НЕ трогает status — preserves lead/handoff/finished/manual
historic markers.

D-04 auto-takeover: POST /send updates conversation (ai_enabled=false,
status='manual', paused_reason='Manager sent message via UI'), cancels
pending queue items for the recipient phone, THEN calls Telethon outside
the transaction. Workspace + sender lifecycle/auth_status checked BEFORE
the Telegram call.
"""

import logging
import uuid
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete as sa_delete
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Conversation
from app.schemas import (
    ConversationListResponse,
    ConversationResponse,
    ConversationUpdate,
    DeleteConversationsBatchRequest,
    LLMCallListResponse,
    LLMCallResponse,
    MessageListResponse,
    MessageResponse,
    SendMessageFromUIRequest,
    SendMessageFromUIResponse,
)
from app.services.telegram import telegram_service
from app.utils.auth import AuthCtx, auth_dep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/conversations", tags=["conversations"])


# ── Workspace-scope helpers ───────────────────────────────────────────────────


async def _load_conversation_or_404(
    db: AsyncSession, ctx: AuthCtx, conversation_id: UUID
) -> dict:
    """Return conversation row or raise 404 (cross-workspace = 404, not 403)."""
    row = (await db.execute(text("""
        SELECT id, workspace_id, sender_id, contact_phone, contact_name,
               contact_telegram_id, ai_enabled, ai_context_id, campaign_id,
               status, paused_at, paused_reason, created_at, updated_at
        FROM conversations
        WHERE id = :cid AND workspace_id = :wid
        -- TODO(v2-rls): replaced by RLS policy app.workspace_id
    """), {"cid": str(conversation_id), "wid": str(ctx.workspace_id)})).first()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "CONVERSATION_NOT_FOUND",
                    "message": "Conversation not found"},
        )
    return dict(row._mapping)


# ── List / detail endpoints ───────────────────────────────────────────────────


@router.get("", response_model=ConversationListResponse)
async def list_conversations(
    campaign_id: Optional[UUID] = Query(None),
    agent_id: Optional[UUID] = Query(None),
    sender_id: Optional[UUID] = Query(None),
    status: Optional[str] = Query(None),
    ai_enabled: Optional[bool] = Query(None),
    search: Optional[str] = Query(None),
    limit: int = Query(50, le=100, ge=1),
    offset: int = Query(0, ge=0),
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
) -> ConversationListResponse:
    """INBX-01 + INBX-05 — list workspace conversations with filters.

    D-17: status=None → hide status='bot_ignored' and status='telegram_service'
    (Telegram login/auth-code notifications live in their own 'Telegram' tab,
    reached via ?status=telegram_service).
    Warmup-pair exclude preserved from legacy (workspace boundary added).
    """
    where_clauses = ["c.workspace_id = :wid"]
    params: dict = {"wid": str(ctx.workspace_id), "limit": limit, "offset": offset}

    # D-17: hide bot_ignored + telegram_service unless caller explicitly asks.
    if status is None:
        where_clauses.append("c.status NOT IN ('bot_ignored', 'telegram_service')")
    else:
        where_clauses.append("c.status = :status")
        params["status"] = status

    if campaign_id is not None:
        where_clauses.append("c.campaign_id = :campaign_id")
        params["campaign_id"] = str(campaign_id)
    if agent_id is not None:
        where_clauses.append("c.ai_context_id = :agent_id")
        params["agent_id"] = str(agent_id)
    if sender_id is not None:
        where_clauses.append("c.sender_id = :sender_id")
        params["sender_id"] = str(sender_id)
    if ai_enabled is not None:
        where_clauses.append("c.ai_enabled = :ai_enabled")
        params["ai_enabled"] = ai_enabled
    if search:
        where_clauses.append(
            "(c.contact_phone ILIKE :search OR c.contact_name ILIKE :search)"
        )
        params["search"] = f"%{search}%"

    where_sql = " AND ".join(where_clauses)

    list_query = text(f"""
        SELECT
            c.id, c.workspace_id, c.sender_id, s.slug AS sender_slug,
            c.contact_phone, c.contact_name, c.contact_telegram_id,
            c.ai_enabled, c.ai_context_id, c.campaign_id, c.status,
            c.paused_at, c.paused_reason, c.created_at, c.updated_at,
            last_msg.message_text AS last_message,
            last_msg.created_at   AS last_message_at,
            COALESCE(unread_sq.unread_count, 0) AS unread_count
        FROM conversations c
        JOIN senders s ON c.sender_id = s.id
        LEFT JOIN LATERAL (
            SELECT message_text, created_at FROM messages
            WHERE conversation_id = c.id
            ORDER BY created_at DESC LIMIT 1
        ) last_msg ON true
        LEFT JOIN LATERAL (
            SELECT COUNT(*) AS unread_count FROM messages
            WHERE conversation_id = c.id
              AND direction = 'inbound'
              AND sent_by = 'contact'
        ) unread_sq ON true
        WHERE {where_sql}
          -- warmup-pair exclude (legacy preserved, workspace boundary added).
          AND NOT EXISTS (
              SELECT 1 FROM senders s2
              WHERE s2.workspace_id = :wid
                AND s2.telegram_id = c.contact_telegram_id
                AND s2.telegram_id IS NOT NULL
          )
        ORDER BY c.updated_at DESC
        LIMIT :limit OFFSET :offset
    """)

    count_query = text(f"""
        SELECT COUNT(*) FROM conversations c
        WHERE {where_sql}
          AND NOT EXISTS (
              SELECT 1 FROM senders s2
              WHERE s2.workspace_id = :wid
                AND s2.telegram_id = c.contact_telegram_id
                AND s2.telegram_id IS NOT NULL
          )
    """)

    rows = (await db.execute(list_query, params)).fetchall()
    total = (await db.execute(count_query, params)).scalar() or 0

    return ConversationListResponse(
        conversations=[ConversationResponse(**dict(r._mapping)) for r in rows],
        total=total,
    )


@router.get("/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(
    conversation_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
) -> ConversationResponse:
    """INBX-02 — single conversation with last_message preview."""
    row = (await db.execute(text("""
        SELECT
            c.id, c.workspace_id, c.sender_id, s.slug AS sender_slug,
            c.contact_phone, c.contact_name, c.contact_telegram_id,
            c.ai_enabled, c.ai_context_id, c.campaign_id, c.status,
            c.paused_at, c.paused_reason, c.created_at, c.updated_at,
            last_msg.message_text AS last_message,
            last_msg.created_at AS last_message_at,
            COALESCE(unread_sq.unread_count, 0) AS unread_count
        FROM conversations c
        JOIN senders s ON c.sender_id = s.id
        LEFT JOIN LATERAL (
            SELECT message_text, created_at FROM messages
            WHERE conversation_id = c.id
            ORDER BY created_at DESC LIMIT 1
        ) last_msg ON true
        LEFT JOIN LATERAL (
            SELECT COUNT(*) AS unread_count FROM messages
            WHERE conversation_id = c.id
              AND direction = 'inbound'
              AND sent_by = 'contact'
        ) unread_sq ON true
        WHERE c.id = :cid AND c.workspace_id = :wid
    """), {"cid": str(conversation_id), "wid": str(ctx.workspace_id)})).first()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "CONVERSATION_NOT_FOUND",
                    "message": "Conversation not found"},
        )
    return ConversationResponse(**dict(row._mapping))


@router.get("/{conversation_id}/messages", response_model=MessageListResponse)
async def get_messages(
    conversation_id: UUID,
    limit: int = Query(100, le=200, ge=1),
    offset: int = Query(0, ge=0),
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
) -> MessageListResponse:
    """INBX-02 — message history with pagination.

    Workspace gate via JOIN on conversations. 404 if conversation not in workspace.
    """
    exists = (await db.execute(text("""
        SELECT 1 FROM conversations WHERE id = :cid AND workspace_id = :wid
    """), {"cid": str(conversation_id), "wid": str(ctx.workspace_id)})).first()
    if exists is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "CONVERSATION_NOT_FOUND",
                    "message": "Conversation not found"},
        )

    rows = (await db.execute(text("""
        SELECT m.id, m.conversation_id, m.direction, m.message_text,
               m.sent_by, m.telegram_message_id, m.created_at
        FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        WHERE c.id = :cid AND c.workspace_id = :wid
        ORDER BY m.created_at ASC
        LIMIT :limit OFFSET :offset
    """), {"cid": str(conversation_id), "wid": str(ctx.workspace_id),
           "limit": limit, "offset": offset})).fetchall()

    total = (await db.execute(text("""
        SELECT COUNT(*) FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        WHERE c.id = :cid AND c.workspace_id = :wid
    """), {"cid": str(conversation_id), "wid": str(ctx.workspace_id)})).scalar() or 0

    return MessageListResponse(
        messages=[MessageResponse(**dict(r._mapping)) for r in rows],
        total=total,
    )


# ── Mutating endpoints ────────────────────────────────────────────────────────


@router.patch("/{conversation_id}", response_model=ConversationResponse)
async def update_conversation(
    conversation_id: UUID,
    payload: ConversationUpdate,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
) -> ConversationResponse:
    """Partial update of ai_enabled / status / ai_context_id.

    `status` is validated against CONVERSATION_STATUSES in the Pydantic model.
    """
    await _load_conversation_or_404(db, ctx, conversation_id)

    updates: list[str] = []
    params: dict = {"cid": str(conversation_id), "wid": str(ctx.workspace_id)}
    if payload.ai_enabled is not None:
        updates.append("ai_enabled = :ai_enabled")
        params["ai_enabled"] = payload.ai_enabled
    if payload.status is not None:
        updates.append("status = :status")
        params["status"] = payload.status
    if payload.ai_context_id is not None:
        updates.append("ai_context_id = :aid")
        params["aid"] = str(payload.ai_context_id)

    if updates:
        updates.append("updated_at = NOW()")
        await db.execute(text(f"""
            UPDATE conversations SET {", ".join(updates)}
            WHERE id = :cid AND workspace_id = :wid
        """), params)
        await db.commit()

    return await get_conversation(conversation_id, ctx, db)


@router.post("/{conversation_id}/disable-ai", response_model=ConversationResponse)
async def disable_ai(
    conversation_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
) -> ConversationResponse:
    """INBX-04 / D-01 / D-02 — manual switch to manager mode + cancel queue."""
    await _load_conversation_or_404(db, ctx, conversation_id)

    # D-01: conversation flipped to status='manual', AI off.
    await db.execute(text("""
        UPDATE conversations
        SET ai_enabled = false,
            status = 'manual',
            paused_at = NOW(),
            paused_reason = 'Manager took over',
            updated_at = NOW()
        WHERE id = :cid AND workspace_id = :wid
    """), {"cid": str(conversation_id), "wid": str(ctx.workspace_id)})

    # D-02: cancel any pending queue items targeting this conversation's
    # recipient_phone. Use 'failed' (not 'cancelled') consistent with
    # _handle_antispam_signal — QueueItemStatus enum has 'failed' / 'cancelled'
    # but Phase 4 production code only writes 'failed' for similar cancellations.
    await db.execute(text("""
        UPDATE message_queue
        SET status = 'failed',
            error_message = 'Conversation taken over manually',
            finished_at = NOW()
        WHERE workspace_id = :wid
          AND recipient_phone = (
              SELECT contact_phone FROM conversations WHERE id = :cid
          )
          AND status = 'pending'
    """), {"cid": str(conversation_id), "wid": str(ctx.workspace_id)})

    await db.commit()
    return await get_conversation(conversation_id, ctx, db)


@router.post("/{conversation_id}/enable-ai", response_model=ConversationResponse)
async def enable_ai(
    conversation_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
) -> ConversationResponse:
    """INBX-04 / D-03 — reverse switch: turn AI back on and undo a manual takeover.

    The listener only auto-replies when `ai_enabled=true AND status='active'`
    (services/listener.py). So flipping `ai_enabled=true` alone left a dialog
    that was 'manual' (from disable-ai) mute — the bot stayed silent despite the
    UI toggle being on. Reverse the disable-ai takeover by moving
    'manual' → 'active' as well.

    Legacy bug guard preserved: only 'manual' is reset. Historic markers
    'lead'/'handoff'/'finished'/'bot_ignored' are left intact (a previous
    version set status='active' unconditionally and destroyed them) — UI may
    PATCH /{id} explicitly if a different status change is desired.
    """
    await _load_conversation_or_404(db, ctx, conversation_id)

    await db.execute(text("""
        UPDATE conversations
        SET ai_enabled = true,
            status = CASE WHEN status = 'manual' THEN 'active' ELSE status END,
            paused_at = NULL,
            paused_reason = NULL,
            updated_at = NOW()
        WHERE id = :cid AND workspace_id = :wid
    """), {"cid": str(conversation_id), "wid": str(ctx.workspace_id)})
    await db.commit()
    return await get_conversation(conversation_id, ctx, db)


@router.post("/{conversation_id}/send", response_model=SendMessageFromUIResponse)
async def send_message_from_ui(
    conversation_id: UUID,
    payload: SendMessageFromUIRequest,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
) -> SendMessageFromUIResponse:
    """INBX-04 / D-04 — auto-takeover send from inbox UI.

    Workflow:
      1. Load conversation + sender. Filter on workspace + sender lifecycle/auth.
         (Phase 2 D-11 dropped the senders.active boolean — using
         lifecycle_status + auth_status now.)
      2. Auto-takeover: status='manual', ai_enabled=false, paused_reason set.
      3. Cancel pending queue items for the recipient_phone (D-02 pattern).
      4. Telethon send OUTSIDE the transaction (legacy pattern preserved).
      5. INSERT message row with sent_by='human' on success.
    """
    # 1. Load conversation + sender; workspace + sender-active gate.
    row = (await db.execute(text("""
        SELECT c.contact_telegram_id, c.contact_name,
               s.id AS sender_id, s.slug AS sender_slug,
               s.session_string, s.proxy
        FROM conversations c
        JOIN senders s ON c.sender_id = s.id
        WHERE c.id = :cid
          AND c.workspace_id = :wid
          AND s.lifecycle_status = 'active'
          AND s.auth_status = 'ok'
        -- Phase 2 D-11: legacy senders boolean is DROPPED; using lifecycle_status + auth_status.
        -- TODO(v2-rls): replaced by RLS policy
    """), {"cid": str(conversation_id), "wid": str(ctx.workspace_id)})).first()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "CONVERSATION_NOT_FOUND",
                    "message": "Conversation not found or sender inactive"},
        )
    if row.contact_telegram_id is None:
        raise HTTPException(
            status_code=400,
            detail={"code": "NO_TELEGRAM_ID",
                    "message": "Contact has no Telegram ID"},
        )

    # 2. Auto-takeover UPDATE (D-04).
    await db.execute(text("""
        UPDATE conversations
        SET ai_enabled = false,
            status = 'manual',
            paused_at = NOW(),
            paused_reason = 'Manager sent message via UI',
            updated_at = NOW()
        WHERE id = :cid AND workspace_id = :wid
    """), {"cid": str(conversation_id), "wid": str(ctx.workspace_id)})

    # 3. Cancel pending queue items (D-02 pattern).
    await db.execute(text("""
        UPDATE message_queue
        SET status = 'failed',
            error_message = 'Conversation taken over manually',
            finished_at = NOW()
        WHERE workspace_id = :wid
          AND recipient_phone = (
              SELECT contact_phone FROM conversations WHERE id = :cid
          )
          AND status = 'pending'
    """), {"cid": str(conversation_id), "wid": str(ctx.workspace_id)})

    await db.commit()

    # 4. Telethon send OUTSIDE transaction.
    result = await telegram_service.send_message_by_telegram_id(
        sender_slug=row.sender_slug,
        encrypted_session=row.session_string,
        telegram_id=row.contact_telegram_id,
        message=payload.message,
        proxy=row.proxy,
    )

    if not result.get("success"):
        return SendMessageFromUIResponse(
            success=False,
            error=result.get("error", "Telegram send failed"),
        )

    telegram_message_id = result.get("telegram_message_id")

    # 5. INSERT messages row after Telethon success.
    message_id = uuid.uuid4()
    await db.execute(text("""
        INSERT INTO messages (id, workspace_id, conversation_id, direction,
                              message_text, sent_by, telegram_message_id)
        VALUES (:id, :wid, :cid, 'outbound', :txt, 'human', :tg_mid)
        ON CONFLICT (conversation_id, telegram_message_id) DO NOTHING
    """), {
        "id": str(message_id),
        "wid": str(ctx.workspace_id),
        "cid": str(conversation_id),
        "txt": payload.message,
        "tg_mid": telegram_message_id,
    })
    await db.commit()

    return SendMessageFromUIResponse(
        success=True,
        message_id=message_id,
        telegram_message_id=telegram_message_id,
    )


@router.post("/delete", response_model=dict)
async def delete_conversations_batch(
    payload: DeleteConversationsBatchRequest,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Batch hard delete. Возвращает {deleted: N}.

    Зеркало contacts.delete_contacts_batch: workspace-scoped, cross-tenant ids
    молча пропускаются (не светим существование чужих бесед через 404).
    Один DELETE-statement; FK CASCADE на уровне БД сносит messages + llm_calls
    (как в single-delete ниже). Статический путь /delete объявлен ДО
    DELETE /{conversation_id} и не конфликтует с ним (разные методы/пути).
    """
    result = await db.execute(
        sa_delete(Conversation)
        .where(
            Conversation.id.in_(payload.conversation_ids),
            Conversation.workspace_id == ctx.workspace_id,
            # TODO(v2-rls): replaced by RLS policy app.workspace_id
        )
        .returning(Conversation.id)
        .execution_options(synchronize_session=False)
    )
    deleted = len(result.fetchall())
    await db.commit()
    logger.info(
        f"[conversations] batch-delete workspace={ctx.workspace_id} deleted={deleted}"
    )
    return {"deleted": deleted}


@router.delete("/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Hard delete. FK CASCADE removes messages + llm_calls. 404 на cross-workspace."""
    await _load_conversation_or_404(db, ctx, conversation_id)
    await db.execute(text("""
        DELETE FROM conversations
        WHERE id = :cid AND workspace_id = :wid
    """), {"cid": str(conversation_id), "wid": str(ctx.workspace_id)})
    await db.commit()
    return None


# ── ANLX-05: LLM audit log per conversation ──────────────────────────────────


@router.get("/{conversation_id}/llm-calls", response_model=LLMCallListResponse)
async def get_llm_calls(
    conversation_id: UUID,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
) -> LLMCallListResponse:
    """ANLX-05 — LLM call audit log per conversation (inbox-debug UI).

    Defense-in-depth workspace isolation (T-05-03-WS-ISOLATION):
      1) _load_conversation_or_404 prequery returns 404 on cross-workspace.
      2) SELECT llm_calls also filters WHERE workspace_id = :wid.
    Result is sorted DESC created_at (newest first).
    """
    # Defense-in-depth #1: prequery conversation in workspace
    await _load_conversation_or_404(db, ctx, conversation_id)

    # Defense-in-depth #2: explicit workspace_id filter on llm_calls SELECT
    rows = (await db.execute(text("""
        SELECT id, workspace_id, conversation_id, campaign_id, agent_id, sender_id,
               model, prompt, response_text, tool_calls,
               prompt_tokens, completion_tokens, total_tokens, latency_ms, error,
               created_at
        FROM llm_calls
        WHERE conversation_id = :cid
          AND workspace_id = :wid
        -- TODO(v2-rls): replaced by RLS policy app.workspace_id
        ORDER BY created_at DESC
        LIMIT :limit OFFSET :offset
    """), {
        "cid": str(conversation_id),
        "wid": str(ctx.workspace_id),
        "limit": limit,
        "offset": offset,
    })).fetchall()

    total = (await db.execute(text("""
        SELECT COUNT(*) FROM llm_calls
        WHERE conversation_id = :cid AND workspace_id = :wid
    """), {
        "cid": str(conversation_id),
        "wid": str(ctx.workspace_id),
    })).scalar() or 0

    return LLMCallListResponse(
        llm_calls=[LLMCallResponse(**dict(r._mapping)) for r in rows],
        total=total,
    )
