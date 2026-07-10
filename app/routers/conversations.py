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

D-03: enable-ai resets manager-takeover states 'manual' and 'handoff' → 'active'
(so the listener resumes AI and the handoff badge clears); genuine conversation
outcomes 'lead'/'finished'/'bot_ignored' are preserved as historic markers.

D-04 auto-takeover: POST /send updates conversation (ai_enabled=false,
status='manual', paused_reason='Manager sent message via UI'), cancels
pending queue items for the recipient phone, THEN calls Telethon outside
the transaction. Workspace + sender lifecycle/auth_status checked BEFORE
the Telegram call.
"""

import asyncio
import logging
import os
import tempfile
import uuid
from typing import Optional
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
)
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
    EditMessageRequest,
    LLMCallListResponse,
    LLMCallResponse,
    MessageListResponse,
    MessageResponse,
    SendFileFromUIResponse,
    SendMessageFromUIResponse,
    SendMessageFromUIRequest,
)
from app.services.telegram import telegram_service
from app.services.webhook_notify import notify_signal
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


# ── Phase 23: inbox message-mutation helpers (D-17 error codes, D-19 gate) ─────

# Structured error codes returned by the TelegramService inbox-mutation methods
# (plans 23-02 / 23-05) → HTTP status. Any code not listed collapses to 502
# TELEGRAM_OP_FAILED (see _raise_inbox_message_error).
_INBOX_ERROR_STATUS: dict[str, int] = {
    "MESSAGE_EDIT_TOO_OLD": 409,
    "MESSAGE_NOT_EDITABLE": 422,
    "DELETE_FAILED": 502,
    "FILE_TOO_LARGE": 413,
    "NO_TELEGRAM_ID": 400,
    "RECIPIENT_NOT_IN_TELEGRAM": 422,
    "FLOOD_WAIT": 429,
    "ACCOUNT_FROZEN": 409,
    "USER_IS_BLOCKED": 409,
    "MEDIA_UNAVAILABLE": 410,
    "DOWNLOAD_FAILED": 502,
}


def _raise_inbox_message_error(result: dict) -> None:
    """Map a failed service-method result dict → HTTPException (D-17).

    Accepts ``{"success": False, "error": {"code", "message", "retry_after"?}}``
    — the shape returned by telegram_service.edit_message_by_telegram_id /
    delete_message_by_telegram_id / send_file_by_telegram_id /
    download_media_by_telegram_id. Unknown codes collapse to 502
    TELEGRAM_OP_FAILED; FLOOD_WAIT passes ``retry_after`` through when present.
    """
    err = (result or {}).get("error") or {}
    code = err.get("code") or "TELEGRAM_OP_FAILED"
    message = err.get("message") or "Telegram operation failed"
    if code in _INBOX_ERROR_STATUS:
        status = _INBOX_ERROR_STATUS[code]
        detail: dict = {"code": code, "message": message}
    else:
        status = 502
        detail = {"code": "TELEGRAM_OP_FAILED", "message": message}
    if err.get("retry_after") is not None:
        detail["retry_after"] = err["retry_after"]
    raise HTTPException(status_code=status, detail=detail)


async def _load_message_for_mutation(
    db: AsyncSession,
    ctx: AuthCtx,
    conversation_id: UUID,
    message_id: UUID,
    *,
    require_type_text: bool = False,
):
    """Load an OUTBOUND message row for edit/delete or raise an opaque 404 (D-19).

    Cross-workspace, inbound, contact-sent, wrong-conversation, or (when
    ``require_type_text``) non-text messages ALL collapse to the same
    ``MESSAGE_NOT_FOUND`` 404 — silent tenant isolation, no existence leak.
    Returns the joined message + conversation-contact + sender-session row
    needed to drive the Telethon op (INVERTED ordering: caller runs Telethon
    FIRST, then the DB write — no takeover, D-04/D-08).
    """
    type_clause = " AND m.message_type = 'text'" if require_type_text else ""
    row = (await db.execute(text(f"""
        SELECT m.id AS message_id, m.telegram_message_id, m.direction, m.sent_by,
               m.message_type,
               c.contact_telegram_id, c.contact_phone,
               s.id AS sender_id, s.slug AS sender_slug, s.session_string,
               s.proxy, s.client_fingerprint
        FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        JOIN senders s ON s.id = c.sender_id
        WHERE m.id = :mid AND m.conversation_id = :cid AND c.workspace_id = :wid
          AND m.direction = 'outbound' AND m.sent_by IN ('ai', 'human'){type_clause}
        -- TODO(v2-rls): replaced by RLS policy app.workspace_id
    """), {
        "mid": str(message_id),
        "cid": str(conversation_id),
        "wid": str(ctx.workspace_id),
    })).first()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "MESSAGE_NOT_FOUND", "message": "Message not found"},
        )
    return row


# ── Phase 23: multipart upload streaming size-guard (D-10) ────────────────────

MAX_FILE_BYTES = 50 * 1024 * 1024  # ~50 MB (D-10)


async def _spool_upload_with_cap(upload: UploadFile) -> tuple[str, int]:
    """Stream a multipart upload to a temp file with a hard 50 MB cap.

    NEVER trusts ``Content-Length`` (spoofable, Pitfall 7) and NEVER loads the
    whole upload into RAM — reads in 1 MB chunks and aborts the moment the
    running total crosses ``MAX_FILE_BYTES`` (413 FILE_TOO_LARGE). The temp file
    is unlinked on any error so an oversize/aborted upload leaves nothing behind;
    on success the caller owns ``tmp_path`` and MUST unlink it (D-14).

    The temp file keeps the original extension (e.g. ``.png``) because Telethon's
    photo/mime detection (``utils.is_image`` / ``mimetypes.guess_type``) keys off
    the file PATH's extension, not any filename passed separately — an
    extension-less ``mkstemp()`` path always sends as a generic document.
    """
    suffix = os.path.splitext(upload.filename or "")[1]
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    total = 0
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_FILE_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail={"code": "FILE_TOO_LARGE",
                                "message": "Файл больше 50 МБ"},
                    )
                await asyncio.to_thread(out.write, chunk)
        return tmp_path, total
    except BaseException:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


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
               m.sent_by, m.telegram_message_id, m.created_at,
               m.message_type, m.file_name, m.mime_type, m.size_bytes, m.edited_at
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


# ── Phase 23: edit / delete a past message (INVERTED ordering, NO takeover) ────


@router.patch(
    "/{conversation_id}/messages/{message_id}", response_model=MessageResponse
)
async def edit_message(
    conversation_id: UUID,
    message_id: UUID,
    payload: EditMessageRequest,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """INBM-02 / D-08 — edit an outbound TEXT message for everyone (NO takeover).

    Inverted vs POST /send: the Telethon edit runs FIRST, then the DB write.
    This mutates a PAST message and MUST NOT auto-takeover — it never touches
    conversations.status / ai_enabled / paused_reason / message_queue.

    Gate (D-01/D-05/D-19): only an outbound, ai/human-sent, TEXT message in this
    workspace+conversation is editable; anything else → opaque 404.
    """
    row = await _load_message_for_mutation(
        db, ctx, conversation_id, message_id, require_type_text=True
    )

    # Telethon edit OUTSIDE any transaction (Pitfall 1: no pre-gate on the
    # edit window — attempt then map the error; MessageNotModified = no-op OK).
    result = await telegram_service.edit_message_by_telegram_id(
        sender_slug=row.sender_slug,
        sender_id=str(row.sender_id),
        encrypted_session=row.session_string,
        telegram_id=row.contact_telegram_id,
        telegram_message_id=row.telegram_message_id,
        new_text=payload.message,
        proxy=row.proxy,
        fingerprint=row.client_fingerprint,
    )
    if not result.get("success"):
        _raise_inbox_message_error(result)

    # On success (incl. no_op): persist the new text + edited_at marker.
    await db.execute(text("""
        UPDATE messages
        SET message_text = :txt, edited_at = NOW()
        WHERE id = :mid
    """), {"txt": payload.message, "mid": str(message_id)})
    await db.commit()

    updated = (await db.execute(text("""
        SELECT m.id, m.conversation_id, m.direction, m.message_text, m.sent_by,
               m.telegram_message_id, m.created_at,
               m.message_type, m.file_name, m.mime_type, m.size_bytes, m.edited_at
        FROM messages m
        WHERE m.id = :mid
    """), {"mid": str(message_id)})).first()
    return MessageResponse(**dict(updated._mapping))


@router.delete("/{conversation_id}/messages/{message_id}", status_code=204)
async def delete_message(
    conversation_id: UUID,
    message_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """INBM-01 / D-04 — delete-for-everyone (revoke) + hard-delete the row.

    Inverted vs POST /send: the Telethon revoke runs FIRST, then the DB delete.
    This mutates a PAST message and MUST NOT auto-takeover — it never touches
    conversations.status / ai_enabled / message_queue. The list-preview LATERAL
    subquery auto-recomputes last_message from the next remaining row (D-03).

    Gate (D-01/D-19): any outbound ai/human-sent message in this workspace+
    conversation is deletable (no text-type requirement); anything else → 404.
    DELETE_FAILED is reserved for real connection/flood/frozen failures — a
    stale/own-message revoke is a silent Telegram no-op = success (Pitfall 4).
    """
    row = await _load_message_for_mutation(db, ctx, conversation_id, message_id)

    # Telethon revoke (revoke=True is the service default) OUTSIDE any txn.
    result = await telegram_service.delete_message_by_telegram_id(
        sender_slug=row.sender_slug,
        sender_id=str(row.sender_id),
        encrypted_session=row.session_string,
        telegram_id=row.contact_telegram_id,
        telegram_message_id=row.telegram_message_id,
        proxy=row.proxy,
        fingerprint=row.client_fingerprint,
    )
    if not result.get("success"):
        _raise_inbox_message_error(result)

    # On success: hard-delete the row (D-03, DB row is the source of truth).
    await db.execute(
        text("DELETE FROM messages WHERE id = :mid"), {"mid": str(message_id)}
    )
    await db.commit()
    return None


# ── Phase 23: send a file from inbox (NEW outbound → auto-takeover, D-12) ──────


@router.post("/{conversation_id}/send-file", response_model=SendFileFromUIResponse)
async def send_file_from_ui(
    conversation_id: UUID,
    file: UploadFile = File(...),
    caption: Optional[str] = Form(None),
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
) -> SendFileFromUIResponse:
    """INBM-03 / D-12 — send a file from inbox WITH auto-takeover (mirrors POST /send).

    Unlike edit/delete (past-message mutations, no takeover), sending a file is a
    NEW outbound intervention → it flips the conversation to manual and cancels the
    pending queue, exactly like POST /send. Ordering mirrors POST /send: gate → spool
    (413 before any takeover) → takeover UPDATE + queue-cancel → commit → Telethon
    OUTSIDE the txn → INSERT a typed messages row. The temp upload is unlinked in a
    finally and the byte payload is NEVER persisted to the DB (D-14).

    D-22: ``caption`` is a brand-new multipart field with no legacy/Lovable naming
    precedent (unlike message/message_text), so it needs NO Form alias. The persisted
    ``message_type`` is a BEST-EFFORT label off the browser ``file.content_type``;
    actual Telegram rendering is governed by ``force_document=False`` (Telethon
    auto-detect), so any label/render mismatch is cosmetic only.
    """
    # 1. Load conversation + sender; workspace + sender lifecycle/auth gate
    #    (mirror POST /send, plus client_fingerprint for the send helper).
    row = (await db.execute(text("""
        SELECT c.contact_telegram_id,
               s.id AS sender_id, s.slug AS sender_slug,
               s.session_string, s.proxy, s.client_fingerprint
        FROM conversations c
        JOIN senders s ON c.sender_id = s.id
        WHERE c.id = :cid
          AND c.workspace_id = :wid
          AND s.lifecycle_status = 'active'
          AND s.auth_status = 'ok'
        -- TODO(v2-rls): replaced by RLS policy app.workspace_id
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

    # 2. Stream the upload to a temp file with a 50 MB cap (413 BEFORE any
    #    takeover/Telethon so an oversize upload changes nothing).
    tmp_path, _size = await _spool_upload_with_cap(file)

    try:
        # 3. Auto-takeover UPDATE (D-12, mirrors POST /send step 2).
        await db.execute(text("""
            UPDATE conversations
            SET ai_enabled = false,
                status = 'manual',
                paused_at = NOW(),
                paused_reason = 'Manager sent file via UI',
                updated_at = NOW()
            WHERE id = :cid AND workspace_id = :wid
        """), {"cid": str(conversation_id), "wid": str(ctx.workspace_id)})

        # 4. Cancel pending queue items for the recipient_phone (D-02 pattern).
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

        # 5. Commit takeover BEFORE the Telegram call (legacy pattern).
        await db.commit()

        # 6. Telethon send OUTSIDE the transaction.
        result = await telegram_service.send_file_by_telegram_id(
            sender_slug=row.sender_slug,
            sender_id=str(row.sender_id),
            encrypted_session=row.session_string,
            telegram_id=row.contact_telegram_id,
            tmp_path=tmp_path,
            file_name=file.filename,
            caption=caption,
            proxy=row.proxy,
            fingerprint=row.client_fingerprint,
        )

        # 7. Map a failed send to a structured error (D-17).
        if not result.get("success"):
            _raise_inbox_message_error(result)

        telegram_message_id = result.get("telegram_message_id")

        # 8. Best-effort message_type off the browser mime (matches Telethon
        #    auto-media): image/* → photo, video/* → video, else document.
        ct = (file.content_type or "").lower()
        if ct.startswith("image/"):
            mtype = "photo"
        elif ct.startswith("video/"):
            mtype = "video"
        else:
            mtype = "document"

        # 9. INSERT a typed messages row (byte payload NEVER stored, D-14).
        message_id = uuid.uuid4()
        await db.execute(text("""
            INSERT INTO messages (id, workspace_id, conversation_id, direction,
                                  message_text, sent_by, telegram_message_id,
                                  message_type, file_name, mime_type, size_bytes)
            VALUES (:id, :wid, :cid, 'outbound', :cap, 'human', :tg_mid,
                    :mtype, :fname, :mime, :size)
            ON CONFLICT (conversation_id, telegram_message_id) DO NOTHING
        """), {
            "id": str(message_id),
            "wid": str(ctx.workspace_id),
            "cid": str(conversation_id),
            "cap": caption,
            "tg_mid": telegram_message_id,
            "mtype": mtype,
            "fname": file.filename,
            "mime": file.content_type,
            "size": _size,
        })
        await db.commit()

        return SendFileFromUIResponse(
            success=True,
            message_id=message_id,
            telegram_message_id=telegram_message_id,
            message_type=mtype,
        )
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


@router.get("/{conversation_id}/messages/{message_id}/download")
async def download_message_file(
    conversation_id: UUID,
    message_id: UUID,
    disposition: str = Query("attachment"),
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """INBM-05 / D-16 — lazy on-demand download of an incoming file.

    The listener (23-04) records incoming media as metadata-only rows; the bytes
    are fetched from Telegram only when the manager clicks download. Gate is the
    INBOUND counterpart to _load_message_for_mutation: the message must belong to
    this conversation+workspace and carry media (does NOT require outbound). Bytes
    are streamed straight through — NEVER persisted (D-16). ``?disposition=inline``
    is optional (OQ3); the default is ``attachment``.
    """
    # 1. Media-message gate (workspace-scoped, inbound-friendly, opaque 404).
    row = (await db.execute(text("""
        SELECT m.telegram_message_id, m.file_name, m.mime_type,
               c.contact_telegram_id,
               s.id AS sender_id, s.slug AS sender_slug, s.session_string,
               s.proxy, s.client_fingerprint
        FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        JOIN senders s ON s.id = c.sender_id
        WHERE m.id = :mid AND m.conversation_id = :cid AND c.workspace_id = :wid
          AND m.message_type IN ('photo', 'video', 'voice', 'document')
        -- TODO(v2-rls): replaced by RLS policy app.workspace_id
    """), {
        "mid": str(message_id),
        "cid": str(conversation_id),
        "wid": str(ctx.workspace_id),
    })).first()

    if row is None or row.telegram_message_id is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "MESSAGE_NOT_FOUND", "message": "Message not found"},
        )

    # 2. Fetch bytes from Telegram (lazy). Never persisted.
    result = await telegram_service.download_media_by_telegram_id(
        sender_slug=row.sender_slug,
        sender_id=str(row.sender_id),
        encrypted_session=row.session_string,
        telegram_id=row.contact_telegram_id,
        telegram_message_id=row.telegram_message_id,
        proxy=row.proxy,
        fingerprint=row.client_fingerprint,
    )

    # 3. Normalize the service result: the real method returns a dict
    #    ({"success", "data", "mime", "name"} | {"success": False, "error": ...});
    #    a bare-bytes / None shape is also tolerated (defensive).
    data = None
    svc_mime = None
    svc_name = None
    if result is None:
        raise HTTPException(
            status_code=410,
            detail={"code": "MEDIA_UNAVAILABLE",
                    "message": "Файл больше недоступен в Telegram"},
        )
    if isinstance(result, dict):
        if not result.get("success"):
            _raise_inbox_message_error(result)
        data = result.get("data")
        svc_mime = result.get("mime")
        svc_name = result.get("name")
    else:
        data = result  # raw bytes

    if data is None:
        raise HTTPException(
            status_code=410,
            detail={"code": "MEDIA_UNAVAILABLE",
                    "message": "Файл больше недоступен в Telegram"},
        )

    # 4. Stream bytes with correct headers (mirror PROF-07 byte-serving).
    name = row.file_name or svc_name or "file"
    mime = row.mime_type or svc_mime or "application/octet-stream"
    disp = "inline" if disposition == "inline" else "attachment"
    return Response(
        content=data,
        media_type=mime,
        headers={"Content-Disposition": f'{disp}; filename="{name}"'},
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
    """INBX-04 / D-03 — reverse switch: turn AI back on and undo a takeover.

    The listener only auto-replies when `ai_enabled=true AND status='active'`
    (services/listener.py). So flipping `ai_enabled=true` alone left a dialog
    that was 'manual' (from disable-ai) OR 'handoff' (from the AI's
    transfer_to_manager auto-pause) mute — the bot stayed silent despite the UI
    toggle being on, and the 'handoff' badge never cleared. Both 'manual' and
    'handoff' are manager-takeover states (AI paused, human handling); an
    explicit "switch back to AI" must reset either → 'active' so the listener
    resumes and the badge clears.

    Legacy bug guard preserved for genuine conversation outcomes: 'lead',
    'finished' and 'bot_ignored' are left intact (a previous version set
    status='active' unconditionally and destroyed them) — UI may PATCH /{id}
    explicitly if a different status change is desired.
    """
    await _load_conversation_or_404(db, ctx, conversation_id)

    await db.execute(text("""
        UPDATE conversations
        SET ai_enabled = true,
            status = CASE WHEN status IN ('manual', 'handoff') THEN 'active' ELSE status END,
            paused_at = NULL,
            paused_reason = NULL,
            updated_at = NOW()
        WHERE id = :cid AND workspace_id = :wid
    """), {"cid": str(conversation_id), "wid": str(ctx.workspace_id)})
    await db.commit()
    return await get_conversation(conversation_id, ctx, db)


@router.post("/{conversation_id}/mark-lead", response_model=ConversationResponse)
async def mark_lead(
    conversation_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
) -> ConversationResponse:
    """Manual 'mark as lead' from the inbox UI.

    Mirrors ai_engine._handle_builtin_signal(mark_as_lead): set status='lead'
    (ai_enabled UNCHANGED — lead is a marker, the conversation continues) and
    fire the campaign lead webhook (fire-and-forget). 404 if not in this
    workspace. The existing PATCH /{id} only sets status and does NOT fire the
    webhook, so downstream (n8n) consumers need this dedicated endpoint to see
    the same 'lead' event the AI's mark_as_lead signal produces.
    """
    await _load_conversation_or_404(db, ctx, conversation_id)

    # UPDATE status only — never touch ai_enabled (matches auto-lead flow).
    await db.execute(text("""
        UPDATE conversations SET status='lead', updated_at=NOW()
        WHERE id = :cid AND workspace_id = :wid
    """), {"cid": str(conversation_id), "wid": str(ctx.workspace_id)})
    await db.commit()

    # Lean SELECT of just the webhook + contact fields notify_signal needs.
    # No contact_id FK: LEFT JOIN contacts on (workspace_id, phone).
    row = (await db.execute(text("""
        SELECT c.campaign_id,
               camp.id AS camp_id, camp.name AS camp_name,
               camp.workspace_id AS camp_wid,
               camp.lead_webhook_url, camp.webhook_url,
               c.contact_phone, c.contact_telegram_id, c.contact_name,
               ct.full_name AS ct_full_name, ct.username AS ct_username,
               ct.source AS ct_source, ct.custom AS ct_custom
        FROM conversations c
        LEFT JOIN campaigns camp ON camp.id = c.campaign_id
        LEFT JOIN contacts ct
            ON ct.workspace_id = c.workspace_id AND ct.phone = c.contact_phone
        WHERE c.id = :cid AND c.workspace_id = :wid
    """), {"cid": str(conversation_id), "wid": str(ctx.workspace_id)})).first()

    campaign: dict = {}
    contact: dict = {}
    if row is not None:
        if row.camp_id is not None:
            campaign = {
                "id": row.camp_id,
                "name": row.camp_name,
                "workspace_id": row.camp_wid,
                "lead_webhook_url": row.lead_webhook_url,
                "webhook_url": row.webhook_url,
            }
        contact = {
            "phone": row.contact_phone,
            "telegram_id": row.contact_telegram_id,
            "full_name": row.ct_full_name or row.contact_name,
            "username": row.ct_username,
            "source": row.ct_source,
            "custom": row.ct_custom or {},
        }

    # Fire-and-forget AFTER commit (never await webhook inside a txn).
    # notify_signal itself no-ops when both URLs are None.
    await notify_signal(
        event_type="lead",
        campaign=campaign,
        conversation_id=conversation_id,
        contact=contact,
        reason="Marked as lead manually via UI",
        db=db,
    )

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
        sender_id=str(row.sender_id),
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
