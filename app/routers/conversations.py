from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from typing import Optional
from datetime import datetime
from pydantic import BaseModel
from uuid import UUID

from app.database import get_db
from app.routers.auth import verify_api_key

router = APIRouter(prefix="/api/v1/conversations", tags=["conversations"])


# === Schemas ===
class ConversationResponse(BaseModel):
    id: UUID
    sender_slug: str
    contact_phone: str
    contact_name: Optional[str]
    contact_telegram_id: Optional[int]
    ai_enabled: bool
    ai_context_id: Optional[UUID]
    status: str
    paused_at: Optional[datetime]
    paused_reason: Optional[str]
    created_at: datetime
    updated_at: datetime
    last_message: Optional[str] = None
    last_message_at: Optional[datetime] = None
    unread_count: int = 0


class ConversationUpdate(BaseModel):
    ai_enabled: Optional[bool] = None
    ai_context_id: Optional[UUID] = None
    status: Optional[str] = None


class MessageResponse(BaseModel):
    id: UUID
    direction: str
    message_text: str
    sent_by: str
    telegram_message_id: Optional[int]
    created_at: datetime


class ConversationListResponse(BaseModel):
    conversations: list[ConversationResponse]
    total: int


class MessagesListResponse(BaseModel):
    messages: list[MessageResponse]
    total: int


# === Endpoints ===
@router.get("", response_model=ConversationListResponse)
async def list_conversations(
    status: Optional[str] = Query(None, description="Filter by status: active, paused, manual"),
    ai_enabled: Optional[bool] = Query(None, description="Filter by AI status"),
    limit: int = Query(50, le=100),
    offset: int = Query(0),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key)
):
    """Список всех диалогов с последним сообщением"""
    
    # Base query
    query = """
        SELECT
            c.id, s.slug as sender_slug, c.contact_phone, c.contact_name,
            c.contact_telegram_id, c.ai_enabled, c.ai_context_id, c.status,
            c.paused_at, c.paused_reason, c.created_at, c.updated_at,
            last_msg.message_text as last_message,
            last_msg.created_at as last_message_at,
            COALESCE(unread_sq.unread_count, 0) as unread_count
        FROM conversations c
        JOIN senders s ON c.sender_id = s.id
        LEFT JOIN LATERAL (
            SELECT message_text, created_at
            FROM messages
            WHERE conversation_id = c.id
            ORDER BY created_at DESC
            LIMIT 1
        ) last_msg ON true
        LEFT JOIN LATERAL (
            SELECT COUNT(*) as unread_count
            FROM messages
            WHERE conversation_id = c.id AND direction = 'inbound' AND sent_by = 'contact'
        ) unread_sq ON true
        WHERE 1=1
        AND NOT EXISTS (
            SELECT 1 FROM senders s2
            JOIN warmup_pool wp ON wp.sender_id = s2.id
            WHERE s2.telegram_id = c.contact_telegram_id
              AND s2.telegram_id IS NOT NULL
        )
    """
    params = {}
    
    if status:
        query += " AND c.status = :status"
        params["status"] = status
    
    if ai_enabled is not None:
        query += " AND c.ai_enabled = :ai_enabled"
        params["ai_enabled"] = ai_enabled
    
    # Count total
    count_query = f"SELECT COUNT(*) FROM ({query}) sub"
    count_result = await db.execute(text(count_query), params)
    total = count_result.scalar()
    
    # Get data with pagination
    query += " ORDER BY c.updated_at DESC LIMIT :limit OFFSET :offset"
    params["limit"] = limit
    params["offset"] = offset
    
    result = await db.execute(text(query), params)
    rows = result.fetchall()
    
    conversations = []
    for row in rows:
        conversations.append(ConversationResponse(
            id=row[0],
            sender_slug=row[1],
            contact_phone=row[2],
            contact_name=row[3],
            contact_telegram_id=row[4],
            ai_enabled=row[5],
            ai_context_id=row[6],
            status=row[7],
            paused_at=row[8],
            paused_reason=row[9],
            created_at=row[10],
            updated_at=row[11],
            last_message=row[12][:50] + "..." if row[12] and len(row[12]) > 50 else row[12],
            last_message_at=row[13],
            unread_count=row[14] if row[14] is not None else 0
        ))
    
    return ConversationListResponse(conversations=conversations, total=total)


@router.get("/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(
    conversation_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key)
):
    """Получить детали диалога"""
    
    query = """
        SELECT 
            c.id, s.slug as sender_slug, c.contact_phone, c.contact_name, 
            c.contact_telegram_id, c.ai_enabled, c.ai_context_id, c.status,
            c.paused_at, c.paused_reason, c.created_at, c.updated_at
        FROM conversations c
        JOIN senders s ON c.sender_id = s.id
        WHERE c.id = :id
    """
    
    result = await db.execute(text(query), {"id": str(conversation_id)})
    row = result.fetchone()
    
    if not row:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    return ConversationResponse(
        id=row[0],
        sender_slug=row[1],
        contact_phone=row[2],
        contact_name=row[3],
        contact_telegram_id=row[4],
        ai_enabled=row[5],
        ai_context_id=row[6],
        status=row[7],
        paused_at=row[8],
        paused_reason=row[9],
        created_at=row[10],
        updated_at=row[11]
    )


@router.patch("/{conversation_id}", response_model=ConversationResponse)
async def update_conversation(
    conversation_id: UUID,
    update: ConversationUpdate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key)
):
    """Обновить настройки диалога (включить/выключить AI)"""
    
    # Build update query
    updates = []
    params = {"id": str(conversation_id)}
    
    if update.ai_enabled is not None:
        updates.append("ai_enabled = :ai_enabled")
        params["ai_enabled"] = update.ai_enabled
        
        if update.ai_enabled:
            updates.append("status = 'active'")
            updates.append("paused_at = NULL")
            updates.append("paused_reason = NULL")
    
    if update.ai_context_id is not None:
        updates.append("ai_context_id = :ai_context_id")
        params["ai_context_id"] = str(update.ai_context_id)
    
    if update.status is not None:
        updates.append("status = :status")
        params["status"] = update.status
    
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    
    updates.append("updated_at = NOW()")
    
    query = f"UPDATE conversations SET {', '.join(updates)} WHERE id = :id"
    await db.execute(text(query), params)
    await db.commit()
    
    # Return updated conversation
    return await get_conversation(conversation_id, db, _)


@router.get("/{conversation_id}/messages", response_model=MessagesListResponse)
async def get_messages(
    conversation_id: UUID,
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key)
):
    """Получить историю сообщений диалога"""
    
    # Check conversation exists
    check = await db.execute(
        text("SELECT id FROM conversations WHERE id = :id"),
        {"id": str(conversation_id)}
    )
    if not check.fetchone():
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    # Count total
    count_result = await db.execute(
        text("SELECT COUNT(*) FROM messages WHERE conversation_id = :id"),
        {"id": str(conversation_id)}
    )
    total = count_result.scalar()
    
    # Get messages
    result = await db.execute(
        text("""
            SELECT id, direction, message_text, sent_by, telegram_message_id, created_at
            FROM messages
            WHERE conversation_id = :id
            ORDER BY created_at DESC
            LIMIT :limit OFFSET :offset
        """),
        {"id": str(conversation_id), "limit": limit, "offset": offset}
    )
    rows = result.fetchall()
    
    messages = [
        MessageResponse(
            id=row[0],
            direction=row[1],
            message_text=row[2],
            sent_by=row[3],
            telegram_message_id=row[4],
            created_at=row[5]
        )
        for row in rows
    ]
    
    return MessagesListResponse(messages=messages, total=total)


@router.post("/{conversation_id}/enable-ai")
async def enable_ai(
    conversation_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key)
):
    """Быстрое включение AI для диалога"""
    
    await db.execute(
        text("""
            UPDATE conversations 
            SET ai_enabled = true, status = 'active', paused_at = NULL, paused_reason = NULL, updated_at = NOW()
            WHERE id = :id
        """),
        {"id": str(conversation_id)}
    )
    await db.commit()
    
    return {"success": True, "message": "AI enabled"}


@router.post("/{conversation_id}/disable-ai")
async def disable_ai(
    conversation_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key)
):
    """Быстрое выключение AI для диалога"""
    
    await db.execute(
        text("""
            UPDATE conversations 
            SET ai_enabled = false, status = 'manual', paused_at = NOW(), paused_reason = 'Manually disabled', updated_at = NOW()
            WHERE id = :id
        """),
        {"id": str(conversation_id)}
    )
    await db.commit()
    
    return {"success": True, "message": "AI disabled"}


# === Send Message from UI ===
class SendMessageFromUI(BaseModel):
    message: str


class SendMessageFromUIResponse(BaseModel):
    success: bool
    message_id: Optional[UUID] = None
    telegram_message_id: Optional[int] = None
    error: Optional[str] = None


@router.post("/{conversation_id}/send", response_model=SendMessageFromUIResponse)
async def send_message_from_ui(
    conversation_id: UUID,
    request: SendMessageFromUI,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key)
):
    """
    Отправить сообщение в существующий диалог от имени человека (не AI).
    Используется когда менеджер берёт управление диалогом.
    """
    import uuid
    from app.services.telegram import telegram_service
    
    # 1. Получаем данные диалога и sender
    result = await db.execute(
        text("""
            SELECT
                c.contact_telegram_id,
                c.contact_name,
                s.id as sender_id,
                s.slug as sender_slug,
                s.session_string,
                s.proxy
            FROM conversations c
            JOIN senders s ON c.sender_id = s.id
            WHERE c.id = :conv_id AND s.is_active = true
        """),
        {"conv_id": str(conversation_id)}
    )
    row = result.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Conversation not found or sender inactive")

    contact_telegram_id = row[0]
    contact_name = row[1]
    sender_id = row[2]
    sender_slug = row[3]
    encrypted_session = row[4]
    sender_proxy = row[5]

    if not contact_telegram_id:
        raise HTTPException(status_code=400, detail="Contact has no Telegram ID")

    # 2. Отправляем сообщение через Telegram
    try:
        result = await telegram_service.send_message_by_telegram_id(
            sender_slug=sender_slug,
            encrypted_session=encrypted_session,
            telegram_id=contact_telegram_id,
            message=request.message,
            proxy=sender_proxy
        )
        
        if not result["success"]:
            return SendMessageFromUIResponse(
                success=False,
                error=result.get("error", "Failed to send message")
            )
        
        telegram_message_id = result.get("telegram_message_id")
        
    except Exception as e:
        return SendMessageFromUIResponse(
            success=False,
            error=str(e)
        )
    
    # 3. Сохраняем сообщение в БД
    message_id = uuid.uuid4()
    await db.execute(
        text("""
            INSERT INTO messages (id, conversation_id, direction, message_text, sent_by, telegram_message_id)
            VALUES (:id, :conv_id, 'outbound', :msg_text, 'human', :tg_msg_id)
        """),
        {
            "id": str(message_id),
            "conv_id": str(conversation_id),
            "msg_text": request.message,
            "tg_msg_id": telegram_message_id
        }
    )

    # 4. Обновляем updated_at (НЕ отключаем AI - пользователь управляет через переключатель)
    await db.execute(
        text("""
            UPDATE conversations
            SET updated_at = NOW()
            WHERE id = :conv_id
        """),
        {"conv_id": str(conversation_id)}
    )

    await db.commit()
    
    return SendMessageFromUIResponse(
        success=True,
        message_id=message_id,
        telegram_message_id=telegram_message_id
    )


@router.delete("/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key)
):
    """
    Удалить диалог и все его сообщения.
    ВНИМАНИЕ: Это полное удаление (hard delete), данные нельзя восстановить!
    """
    
    # Проверяем существование
    result = await db.execute(
        text("SELECT id FROM conversations WHERE id = :id"),
        {"id": str(conversation_id)}
    )
    if not result.fetchone():
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    # Удаляем сообщения диалога
    await db.execute(
        text("DELETE FROM messages WHERE conversation_id = :id"),
        {"id": str(conversation_id)}
    )
    
    # Удаляем диалог
    await db.execute(
        text("DELETE FROM conversations WHERE id = :id"),
        {"id": str(conversation_id)}
    )
    
    await db.commit()
    
    return None
