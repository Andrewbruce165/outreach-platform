"""
Dual-auth FastAPI dependency для outreach-platform.

Два пути входа:
  1. `Authorization: Bearer <Supabase JWT>` — UI (Lovable frontend, AUTH-02)
  2. `X-Workspace-Key: wsk_<random>`      — Интеграции (n8n, ad-hoc, TENT-03)

Оба резолвятся в `AuthCtx(workspace_id, user_id, source, role)`.

Lazy workspace creation (D-08, TENT-02):
  валидный JWT + нет записи user_workspaces → atomic create в одной транзакции.

# TODO(v2): migrate from python-jose to PyJWT (deprecation — RESEARCH Pitfall 2)
# TODO(v2): migrate JWT validation from HS256 to ES256/JWKS (Supabase
#           default since Oct 2025 — RESEARCH Pitfall 1)
# TODO(v2-rls): app-level workspace filter replaced by Postgres RLS policy
"""

import asyncio
import logging
from typing import Literal, Optional
from uuid import UUID

import bcrypt
from fastapi import Depends, Header, HTTPException
from jose import ExpiredSignatureError, JWTClaimsError, JWTError, jwt
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.config import get_settings
from app.database import get_db
from app.models import UserWorkspace, Workspace, WorkspaceApiKey

logger = logging.getLogger(__name__)
settings = get_settings()


# ─── Public API ──────────────────────────────────────────────────────────────

class AuthCtx(BaseModel):
    """Resolved auth context для текущего запроса (D-12)."""

    workspace_id: UUID
    user_id: Optional[str]                    # supabase 'sub' для JWT, None для API key
    source: Literal["jwt", "api_key"]
    role: Optional[str]                       # 'owner'/'admin'/'member' для JWT, None для API key


async def auth_dep(
    authorization: Optional[str] = Header(None),
    x_workspace_key: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
) -> AuthCtx:
    """
    Главный FastAPI Depends для всех новых endpoint-ов (D-11).

    Branch 1: Authorization: Bearer <JWT> — validate Supabase HS256
    Branch 2: X-Workspace-Key: wsk_...  — bcrypt verify against workspace_api_keys
    No credentials → 401 AUTH_REQUIRED
    """
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
        claims = _decode_supabase_jwt(token)
        return await _resolve_or_create_workspace(
            db,
            supabase_user_id=claims["sub"],
            email=claims.get("email"),
        )

    if x_workspace_key and x_workspace_key.startswith("wsk_"):
        return await _verify_api_key(db, x_workspace_key)

    raise HTTPException(
        status_code=401,
        detail={
            "code": "AUTH_REQUIRED",
            "message": "Provide Authorization Bearer <jwt> or X-Workspace-Key wsk_...",
        },
    )


# ─── Private helpers ─────────────────────────────────────────────────────────

def _decode_supabase_jwt(token: str) -> dict:
    """
    Decode + verify Supabase HS256 JWT.

    Raises HTTPException(401) for expired, invalid claims, or signature errors.
    """
    try:
        claims = jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
            options={"require": ["sub", "exp"]},
        )
    except ExpiredSignatureError:
        raise HTTPException(
            status_code=401,
            detail={"code": "TOKEN_EXPIRED", "message": "JWT expired"},
        )
    except JWTClaimsError as e:
        raise HTTPException(
            status_code=401,
            detail={"code": "TOKEN_INVALID_CLAIMS", "message": str(e)},
        )
    except JWTError:
        # NOTE: ловим общий JWTError, не выдаём конкретику (security best practice)
        raise HTTPException(
            status_code=401,
            detail={"code": "TOKEN_INVALID", "message": "Invalid JWT"},
        )
    return claims


async def _resolve_or_create_workspace(
    db: AsyncSession,
    supabase_user_id: str,
    email: Optional[str],
) -> AuthCtx:
    """
    Найти существующий user_workspaces row → вернуть AuthCtx.
    Если нет — atomic create Workspace + UserWorkspace в одной транзакции (D-08).

    Pitfall 5 (race condition): post-commit re-SELECT защищает от двух параллельных
    первых запросов от одного пользователя — но это hot path в Phase 1
    минимизируется тем, что Lovable обычно делает один POST /auth/me первым.
    """
    # First lookup (no transaction yet)
    result = await db.execute(
        select(UserWorkspace).where(
            UserWorkspace.supabase_user_id == supabase_user_id
        )
        # TODO(v2-rls): когда добавим RLS — этот фильтр станет автоматическим
    )
    uw = result.scalars().first()

    if uw is not None:
        logger.info(
            f"[auth] resolved existing workspace={uw.workspace_id} "
            f"user={supabase_user_id[:8]}..."
        )
        return AuthCtx(
            workspace_id=uw.workspace_id,
            user_id=supabase_user_id,
            source="jwt",
            role=uw.role,
        )

    # Lazy auto-create (D-08, D-09)
    workspace_name = email if email else "My Workspace"

    async with db.begin():
        workspace = Workspace(name=workspace_name)
        db.add(workspace)
        await db.flush()  # получаем workspace.id

        new_uw = UserWorkspace(
            supabase_user_id=supabase_user_id,
            workspace_id=workspace.id,
            role="owner",
        )
        db.add(new_uw)
        # commit на выходе из async with

    # Post-commit re-SELECT (Pitfall 5 защита от race)
    result = await db.execute(
        select(UserWorkspace).where(
            UserWorkspace.supabase_user_id == supabase_user_id
        ).order_by(UserWorkspace.created_at.asc())
    )
    canonical_uw = result.scalars().first()

    logger.info(
        f"[auth] auto-created workspace={canonical_uw.workspace_id} "
        f"name='{workspace_name}' user={supabase_user_id[:8]}..."
    )

    return AuthCtx(
        workspace_id=canonical_uw.workspace_id,
        user_id=supabase_user_id,
        source="jwt",
        role=canonical_uw.role,
    )


async def _verify_api_key(db: AsyncSession, raw_token: str) -> AuthCtx:
    """
    Verify wsk_<...> token: парсим prefix → SELECT активных кандидатов
    → bcrypt verify в asyncio.to_thread (Pitfall 3 — bcrypt sync блокирует loop).
    """
    if len(raw_token) < 12:
        raise HTTPException(
            status_code=401,
            detail={"code": "API_KEY_INVALID", "message": "Malformed workspace key"},
        )

    prefix = raw_token[:12]  # 'wsk_' + 8 chars = 12 (C-02 resolved)

    result = await db.execute(
        select(WorkspaceApiKey).where(
            WorkspaceApiKey.prefix == prefix,
            WorkspaceApiKey.revoked_at.is_(None),
        )
        # TODO(v2-rls): replaced by RLS policy
    )
    candidates = result.scalars().all()

    for candidate in candidates:
        # Pitfall 3: bcrypt sync — обернуть в to_thread
        match = await asyncio.to_thread(
            bcrypt.checkpw,
            raw_token.encode(),
            candidate.bcrypt_hash.encode(),
        )
        if match:
            # Best-effort update last_used_at (не блокируем основной flow)
            candidate.last_used_at = func.now()
            await db.commit()

            logger.info(
                f"[auth] api_key matched workspace={candidate.workspace_id} "
                f"prefix={prefix} key_id={str(candidate.id)[:8]}..."
            )
            return AuthCtx(
                workspace_id=candidate.workspace_id,
                user_id=None,
                source="api_key",
                role=None,
            )

    logger.warning(
        f"[auth] api_key lookup failed prefix={prefix} "
        f"candidates={len(candidates)}"
    )
    raise HTTPException(
        status_code=401,
        detail={
            "code": "API_KEY_INVALID",
            "message": "Invalid or revoked workspace key",
        },
    )
