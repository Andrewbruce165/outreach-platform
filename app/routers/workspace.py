"""
Workspace router (Phase 1 — TENT-03, AUTH-01 UX, AUTH-04 stateless).

Endpoints:
  POST   /api/v1/auth/me                       — bootstrap (JWT only)
  GET    /api/v1/workspace                     — JWT or API key
  PATCH  /api/v1/workspace                     — JWT only (rename)
  POST   /api/v1/workspace/api-keys            — JWT only (plaintext-once)
  GET    /api/v1/workspace/api-keys            — JWT only (без plaintext)
  DELETE /api/v1/workspace/api-keys/{id}       — JWT only (soft-revoke)

Все используют ctx: AuthCtx = Depends(auth_dep).
"""

import asyncio
import logging
import secrets
from datetime import datetime
from typing import List, Optional
from uuid import UUID

import bcrypt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.database import get_db
from app.models import Sender, Workspace, WorkspaceApiKey
from app.utils.auth import AuthCtx, auth_dep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["workspace"])


# === Schemas ===

class AuthMeResponse(BaseModel):
    """Response from POST /auth/me — bootstrap. Triggers TENT-02 lazy create."""
    workspace_id: UUID
    user_id: Optional[str]
    source: str
    role: Optional[str]
    workspace_name: str
    workspace_created_at: datetime


class WorkspaceResponse(BaseModel):
    id: UUID
    name: str
    created_at: datetime
    updated_at: datetime
    # D-20: UI рендерит баннер "Add a dedicated checker account" если false.
    has_checker: bool = False


class WorkspaceUpdate(BaseModel):
    name: str


class ApiKeyCreateRequest(BaseModel):
    name: str  # human-readable label


class ApiKeyCreateResponse(BaseModel):
    """Plaintext token VISIBLE ONLY HERE (D-13). Never returned again."""
    id: UUID
    prefix: str
    name: str
    token: str
    created_at: datetime


class ApiKeyListItem(BaseModel):
    id: UUID
    prefix: str
    name: str
    created_at: datetime
    last_used_at: Optional[datetime]
    revoked_at: Optional[datetime]


class ApiKeyListResponse(BaseModel):
    api_keys: List[ApiKeyListItem]
    total: int


# === Helpers ===

def _require_jwt(ctx: AuthCtx) -> None:
    """Owner-инвариант v1 (D-10): JWT-only endpoints. API key не разрешён."""
    if ctx.source != "jwt":
        raise HTTPException(
            status_code=403,
            detail={
                "code": "JWT_REQUIRED",
                "message": "This endpoint requires JWT auth (not API key)",
            },
        )


async def _workspace_has_checker(db: AsyncSession, workspace_id: UUID) -> bool:
    """D-20: existence-check для sender'а с role='checker' AND auth_status='ok'.

    UI читает этот флаг из GET /workspace и рендерит баннер "Add a dedicated
    checker account to verify phone presence in Telegram" если false.
    """
    result = await db.execute(
        select(func.count(Sender.id)).where(
            Sender.workspace_id == workspace_id,
            Sender.role == "checker",
            Sender.auth_status == "ok",
            # TODO(v2-rls): app-level filter replaced by RLS policy
        )
    )
    return (result.scalar() or 0) > 0


# === Endpoints ===

@router.post("/auth/me", response_model=AuthMeResponse)
async def auth_me(
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """
    Bootstrap endpoint для Lovable frontend (AUTH-01 UX).
    Триггерит lazy auto-create workspace при первом входе пользователя (TENT-02).
    Идемпотентен: повторный вызов возвращает существующий workspace.
    """
    _require_jwt(ctx)

    result = await db.execute(
        select(Workspace).where(Workspace.id == ctx.workspace_id)
        # TODO(v2-rls): app-level filter replaced by RLS policy
    )
    workspace = result.scalars().first()
    if workspace is None:
        # auth_dep гарантирует что workspace_id валиден (или auto-created)
        # если попали сюда — это race на удалении, маловероятно
        raise HTTPException(
            status_code=404,
            detail={"code": "WORKSPACE_NOT_FOUND", "message": "Workspace vanished"},
        )

    return AuthMeResponse(
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        source=ctx.source,
        role=ctx.role,
        workspace_name=workspace.name,
        workspace_created_at=workspace.created_at,
    )


@router.get("/workspace", response_model=WorkspaceResponse)
async def get_workspace(
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Текущий workspace — доступен и через JWT, и через API key (TENT-04)."""
    result = await db.execute(
        select(Workspace).where(Workspace.id == ctx.workspace_id)
        # TODO(v2-rls): app-level filter replaced by RLS policy
    )
    workspace = result.scalars().first()
    if workspace is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "WORKSPACE_NOT_FOUND", "message": "Workspace not found"},
        )

    has_checker = await _workspace_has_checker(db, ctx.workspace_id)

    return WorkspaceResponse(
        id=workspace.id,
        name=workspace.name,
        created_at=workspace.created_at,
        updated_at=workspace.updated_at,
        has_checker=has_checker,
    )


@router.patch("/workspace", response_model=WorkspaceResponse)
async def update_workspace(
    update: WorkspaceUpdate,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Переименование workspace — только JWT (owner)."""
    _require_jwt(ctx)

    if not update.name or len(update.name.strip()) == 0:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_NAME", "message": "name must be non-empty"},
        )

    result = await db.execute(
        select(Workspace).where(Workspace.id == ctx.workspace_id)
        # TODO(v2-rls): app-level filter replaced by RLS policy
    )
    workspace = result.scalars().first()
    if workspace is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "WORKSPACE_NOT_FOUND", "message": "Workspace not found"},
        )

    workspace.name = update.name.strip()
    await db.commit()
    await db.refresh(workspace)

    logger.info(f"[workspace] renamed id={workspace.id} to '{workspace.name}'")

    has_checker = await _workspace_has_checker(db, ctx.workspace_id)

    return WorkspaceResponse(
        id=workspace.id,
        name=workspace.name,
        created_at=workspace.created_at,
        updated_at=workspace.updated_at,
        has_checker=has_checker,
    )


@router.post(
    "/workspace/api-keys", response_model=ApiKeyCreateResponse, status_code=201
)
async def create_api_key(
    request: ApiKeyCreateRequest,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """
    Создание нового wsk_ ключа (TENT-03).
    Plaintext token возвращается ровно ОДИН раз в этом ответе (D-13).
    """
    _require_jwt(ctx)

    raw_secret = secrets.token_urlsafe(32)
    full_token = f"wsk_{raw_secret}"
    prefix = full_token[:12]  # C-02: 12 chars total = 'wsk_' + 8 random

    # Pitfall 3: bcrypt sync — async wrap
    hash_bytes = await asyncio.to_thread(
        bcrypt.hashpw, full_token.encode(), bcrypt.gensalt()
    )

    api_key = WorkspaceApiKey(
        workspace_id=ctx.workspace_id,
        prefix=prefix,
        bcrypt_hash=hash_bytes.decode(),
        name=request.name,
    )
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)

    logger.info(
        f"[api_key] created workspace={ctx.workspace_id} "
        f"prefix={prefix} name='{request.name}' id={api_key.id}"
    )

    return ApiKeyCreateResponse(
        id=api_key.id,
        prefix=api_key.prefix,
        name=api_key.name,
        token=full_token,  # ← VISIBLE ONLY HERE. Никогда больше.
        created_at=api_key.created_at,
    )


@router.get("/workspace/api-keys", response_model=ApiKeyListResponse)
async def list_api_keys(
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Список ключей workspace БЕЗ plaintext (TENT-03)."""
    _require_jwt(ctx)

    result = await db.execute(
        select(WorkspaceApiKey)
        .where(WorkspaceApiKey.workspace_id == ctx.workspace_id)
        # TODO(v2-rls): replaced by RLS policy
        .order_by(WorkspaceApiKey.created_at.desc())
    )
    keys = result.scalars().all()

    items = [
        ApiKeyListItem(
            id=k.id,
            prefix=k.prefix,
            name=k.name,
            created_at=k.created_at,
            last_used_at=k.last_used_at,
            revoked_at=k.revoked_at,
        )
        for k in keys
    ]
    return ApiKeyListResponse(api_keys=items, total=len(items))


@router.delete("/workspace/api-keys/{key_id}", status_code=204)
async def revoke_api_key(
    key_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """
    Soft-revoke ключа: revoked_at = NOW (D-13).
    Cross-tenant защита: WHERE workspace_id == ctx.workspace_id (D-04).
    """
    _require_jwt(ctx)

    result = await db.execute(
        select(WorkspaceApiKey).where(
            WorkspaceApiKey.id == key_id,
            WorkspaceApiKey.workspace_id == ctx.workspace_id,  # cross-tenant guard
            # TODO(v2-rls): replaced by RLS policy
        )
    )
    api_key = result.scalars().first()
    if api_key is None:
        # Не различаем "not found" и "not yours" (security: не раскрываем существование)
        raise HTTPException(
            status_code=404,
            detail={"code": "API_KEY_NOT_FOUND", "message": "Key not found"},
        )

    if api_key.revoked_at is not None:
        # Уже отозван — идемпотентность
        return

    api_key.revoked_at = func.now()
    await db.commit()

    logger.info(
        f"[api_key] revoked workspace={ctx.workspace_id} "
        f"prefix={api_key.prefix} id={key_id}"
    )
