from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from typing import Optional
from datetime import datetime
from pydantic import BaseModel
from uuid import UUID
import json

from app.database import get_db
from app.routers.auth import verify_api_key

router = APIRouter(prefix="/api/v1/contexts", tags=["ai_contexts"])


# === Schemas ===
class ContextCreate(BaseModel):
    name: str
    system_prompt: Optional[str] = None
    tone_of_voice: Optional[str] = None
    rules: Optional[str] = None
    company_info: Optional[str] = None
    product_info: Optional[str] = None
    max_message_length: int = 500
    response_delay_seconds: int = 5
    webhook_functions: Optional[list] = None
    document_webhook_url: Optional[str] = None


class ContextUpdate(BaseModel):
    name: Optional[str] = None
    system_prompt: Optional[str] = None
    tone_of_voice: Optional[str] = None
    rules: Optional[str] = None
    company_info: Optional[str] = None
    product_info: Optional[str] = None
    max_message_length: Optional[int] = None
    response_delay_seconds: Optional[int] = None
    is_active: Optional[bool] = None
    webhook_functions: Optional[list] = None
    document_webhook_url: Optional[str] = None


class ContextResponse(BaseModel):
    id: UUID
    name: str
    system_prompt: Optional[str]
    tone_of_voice: Optional[str]
    rules: Optional[str]
    company_info: Optional[str]
    product_info: Optional[str]
    max_message_length: int
    response_delay_seconds: int
    is_active: bool
    webhook_functions: Optional[list]
    document_webhook_url: Optional[str]
    created_at: datetime
    updated_at: datetime


class ContextListResponse(BaseModel):
    contexts: list[ContextResponse]
    total: int


# === Endpoints ===
@router.get("", response_model=ContextListResponse)
async def list_contexts(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key)
):
    """Список всех AI контекстов (только активные)"""

    result = await db.execute(text("""
        SELECT id, name, system_prompt, tone_of_voice, rules, company_info,
               product_info, max_message_length, response_delay_seconds,
               is_active, webhook_functions, document_webhook_url, created_at, updated_at
        FROM ai_contexts
        WHERE is_active = true
        ORDER BY created_at DESC
    """))
    rows = result.fetchall()

    contexts = [
        ContextResponse(
            id=row[0], name=row[1], system_prompt=row[2], tone_of_voice=row[3],
            rules=row[4], company_info=row[5], product_info=row[6],
            max_message_length=row[7], response_delay_seconds=row[8],
            is_active=row[9], webhook_functions=row[10], document_webhook_url=row[11],
            created_at=row[12], updated_at=row[13]
        )
        for row in rows
    ]
    
    return ContextListResponse(contexts=contexts, total=len(contexts))


@router.post("", response_model=ContextResponse, status_code=201)
async def create_context(
    data: ContextCreate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key)
):
    """Создать новый AI контекст"""

    # Конвертируем webhook_functions в JSON string для PostgreSQL JSONB
    # Используем 'is not None' вместо truthiness check, чтобы пустой список [] тоже конвертировался
    webhook_functions_json = json.dumps(data.webhook_functions) if data.webhook_functions is not None else '[]'

    result = await db.execute(
        text("""
            INSERT INTO ai_contexts (name, system_prompt, tone_of_voice, rules, company_info,
                                     product_info, max_message_length, response_delay_seconds,
                                     webhook_functions, document_webhook_url)
            VALUES (:name, :system_prompt, :tone_of_voice, :rules, :company_info,
                    :product_info, :max_message_length, :response_delay_seconds,
                    CAST(:webhook_functions AS jsonb), :document_webhook_url)
            RETURNING id, name, system_prompt, tone_of_voice, rules, company_info,
                      product_info, max_message_length, response_delay_seconds,
                      COALESCE(is_active, true) as is_active,
                      COALESCE(webhook_functions, '[]'::jsonb) as webhook_functions,
                      document_webhook_url,
                      COALESCE(created_at, NOW()) as created_at,
                      COALESCE(updated_at, NOW()) as updated_at
        """),
        {
            "name": data.name,
            "system_prompt": data.system_prompt,
            "tone_of_voice": data.tone_of_voice,
            "rules": data.rules,
            "company_info": data.company_info,
            "product_info": data.product_info,
            "max_message_length": data.max_message_length,
            "response_delay_seconds": data.response_delay_seconds,
            "webhook_functions": webhook_functions_json,
            "document_webhook_url": data.document_webhook_url
        }
    )
    await db.commit()
    row = result.fetchone()

    return ContextResponse(
        id=row[0], name=row[1], system_prompt=row[2], tone_of_voice=row[3],
        rules=row[4], company_info=row[5], product_info=row[6],
        max_message_length=row[7], response_delay_seconds=row[8],
        is_active=row[9], webhook_functions=row[10], document_webhook_url=row[11],
        created_at=row[12], updated_at=row[13]
    )


@router.get("/{context_id}", response_model=ContextResponse)
async def get_context(
    context_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key)
):
    """Получить AI контекст по ID"""
    
    result = await db.execute(
        text("""
            SELECT id, name, system_prompt, tone_of_voice, rules, company_info,
                   product_info, max_message_length, response_delay_seconds,
                   is_active, webhook_functions, document_webhook_url, created_at, updated_at
            FROM ai_contexts WHERE id = :id
        """),
        {"id": str(context_id)}
    )
    row = result.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Context not found")

    return ContextResponse(
        id=row[0], name=row[1], system_prompt=row[2], tone_of_voice=row[3],
        rules=row[4], company_info=row[5], product_info=row[6],
        max_message_length=row[7], response_delay_seconds=row[8],
        is_active=row[9], webhook_functions=row[10], document_webhook_url=row[11],
        created_at=row[12], updated_at=row[13]
    )


@router.patch("/{context_id}", response_model=ContextResponse)
async def update_context(
    context_id: UUID,
    data: ContextUpdate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key)
):
    """Обновить AI контекст"""

    updates = []
    params = {"id": str(context_id)}

    for field in ["name", "system_prompt", "tone_of_voice", "rules", "company_info",
                  "product_info", "max_message_length", "response_delay_seconds", "is_active",
                  "webhook_functions", "document_webhook_url"]:
        value = getattr(data, field)
        if value is not None:
            # Конвертируем webhook_functions в JSON string для PostgreSQL JSONB
            if field == "webhook_functions":
                updates.append(f"{field} = CAST(:{field} AS jsonb)")
                params[field] = json.dumps(value)
            else:
                updates.append(f"{field} = :{field}")
                params[field] = value

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    updates.append("updated_at = NOW()")

    query = f"UPDATE ai_contexts SET {', '.join(updates)} WHERE id = :id"
    await db.execute(text(query), params)
    await db.commit()
    
    return await get_context(context_id, db, _)


@router.delete("/{context_id}", status_code=204)
async def delete_context(
    context_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key)
):
    """
    Удалить AI контекст (физическое удаление - hard delete)

    Сначала отвязывает контекст от всех senders и conversations,
    затем удаляет сам контекст из базы данных.
    """

    # Проверяем, существует ли контекст
    result = await db.execute(
        text("SELECT id FROM ai_contexts WHERE id = :id"),
        {"id": str(context_id)}
    )
    if not result.fetchone():
        raise HTTPException(status_code=404, detail="Context not found")

    # Шаг 1: Отвязываем контекст от всех senders (SET ai_context_id = NULL)
    await db.execute(
        text("UPDATE senders SET ai_context_id = NULL WHERE ai_context_id = :id"),
        {"id": str(context_id)}
    )

    # Шаг 2: Отвязываем контекст от всех conversations (SET ai_context_id = NULL)
    await db.execute(
        text("UPDATE conversations SET ai_context_id = NULL WHERE ai_context_id = :id"),
        {"id": str(context_id)}
    )

    # Шаг 3: Физическое удаление контекста
    await db.execute(
        text("DELETE FROM ai_contexts WHERE id = :id"),
        {"id": str(context_id)}
    )

    await db.commit()
