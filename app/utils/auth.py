"""
Dual-auth FastAPI dependency для outreach-platform.

Два пути входа:
  1. `Authorization: Bearer <Supabase JWT>` — UI (Lovable frontend, AUTH-02)
  2. `X-Workspace-Key: wsk_<random>`      — Интеграции (n8n, ad-hoc, TENT-03)

Оба резолвятся в `AuthCtx(workspace_id, user_id, source, role)`.

Lazy workspace creation (D-08, TENT-02):
  валидный JWT + нет записи user_workspaces → atomic create в одной транзакции.

# JWT verification (Phase 05.1-DEBUG 2026-05-23):
#   Supabase migrated all projects to **ES256** (asymmetric, EC P-256) signing
#   by default in Oct 2025. The original Phase 05.1 plan was to pin new projects
#   back to HS256 in Supabase Dashboard, but that workaround is fragile —
#   Supabase has been auto-flipping projects forward.
#
#   This module now verifies tokens via the project's published JWKS
#   (`${SUPABASE_URL}/auth/v1/.well-known/jwks.json`) and ES256, with an HS256
#   fallback for legacy projects that explicitly opted into the symmetric
#   algorithm. The decision routes off the JWT `alg` header:
#     - alg=ES256 → verify against JWKS (fetched + cached for 1h)
#     - alg=HS256 → verify against settings.supabase_jwt_secret (legacy)
#     - anything else → 401 TOKEN_INVALID
#
#   The JWKS cache is per-process (each api container warms its own cache on
#   first request, then keeps the keys for an hour). A `kid` miss triggers
#   one refetch (handles graceful key rotation).
#
# TODO(v2): migrate from python-jose to PyJWT (deprecation — RESEARCH Pitfall 2)
# TODO(v2-rls): app-level workspace filter replaced by Postgres RLS policy
"""

import asyncio
import hmac
import logging
import time as _time
from datetime import datetime, timezone
from typing import Literal, Optional
from uuid import UUID

import bcrypt
import httpx
from fastapi import Depends, Header, HTTPException
from jose import jwt
from jose.exceptions import ExpiredSignatureError, JWTClaimsError, JWTError
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models import UserWorkspace, Workspace, WorkspaceApiKey

logger = logging.getLogger(__name__)
settings = get_settings()


# ─── Phase 02.1 (CR-09): in-process token cache ──────────────────────────────
# Before Phase 02.1 every X-Workspace-Key request burned ~100ms CPU on bcrypt
# (12 rounds). On n8n push at 100 RPS that hits 100% CPU on a single api
# container. In-process LRU-ish cache holds successfully-validated tokens for
# 5 minutes — second and subsequent calls with the same raw_token are
# effectively free.
#
# Caveats:
# - Cache is per-process (not shared between containers) — each container
#   warms its own cache on first requests (acceptable).
# - Revoke does NOT invalidate cache immediately — old ctx lives up to TTL.
#   Acceptable in v1 (revoke is rare, 5-min lag tolerable). v2 will need
#   Redis pubsub or periodic DB poll for immediate invalidation.

_TOKEN_CACHE: dict[str, tuple["AuthCtx", float]] = {}
_TOKEN_CACHE_TTL_SECONDS: float = 300.0  # 5 минут
_TOKEN_CACHE_MAX_SIZE: int = 1024         # bounded — eviction = oldest 10%


def _cache_get(raw_token: str) -> Optional["AuthCtx"]:
    entry = _TOKEN_CACHE.get(raw_token)
    if entry is None:
        return None
    ctx, expires_at = entry
    if expires_at < _time.time():
        _TOKEN_CACHE.pop(raw_token, None)
        return None
    return ctx


def _cache_put(raw_token: str, ctx: "AuthCtx") -> None:
    # Bounded eviction: при переполнении дропаем ~10% самых старых entries
    if len(_TOKEN_CACHE) >= _TOKEN_CACHE_MAX_SIZE:
        sorted_keys = sorted(_TOKEN_CACHE.items(), key=lambda kv: kv[1][1])
        drop = max(1, _TOKEN_CACHE_MAX_SIZE // 10)
        for k, _v in sorted_keys[:drop]:
            _TOKEN_CACHE.pop(k, None)
    _TOKEN_CACHE[raw_token] = (ctx, _time.time() + _TOKEN_CACHE_TTL_SECONDS)


# ─── JWKS cache (Phase 05.1-DEBUG, ES256 verification) ───────────────────────
# Supabase publishes its JWT signing keys at
# `${SUPABASE_URL}/auth/v1/.well-known/jwks.json`. We fetch once, cache for 1h,
# refetch on `kid` miss (handles key rotation without restart).
#
# Layout: { "keys_by_kid": {kid: jwk_dict}, "fetched_at": epoch_seconds }
# Lock prevents thundering herd at cold start.

_JWKS_CACHE: dict[str, dict] = {"keys_by_kid": {}, "fetched_at": 0.0}
_JWKS_TTL_SECONDS: float = 3600.0   # 1 час
_JWKS_LOCK: Optional[asyncio.Lock] = None  # lazy-init на первом await (для event-loop binding)


def _jwks_url() -> str:
    base = (settings.supabase_url or "").rstrip("/")
    if not base:
        raise HTTPException(
            status_code=500,
            detail={"code": "AUTH_MISCONFIGURED", "message": "SUPABASE_URL is not set"},
        )
    return f"{base}/auth/v1/.well-known/jwks.json"


async def _fetch_jwks() -> dict[str, dict]:
    """Сходить за свежим JWKS и обновить кеш. Returns keys_by_kid dict."""
    url = _jwks_url()
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        payload = resp.json()
    keys_by_kid = {k["kid"]: k for k in payload.get("keys", []) if "kid" in k}
    _JWKS_CACHE["keys_by_kid"] = keys_by_kid
    _JWKS_CACHE["fetched_at"] = _time.time()
    logger.info(f"[auth] JWKS refreshed: {len(keys_by_kid)} key(s) — kids={list(keys_by_kid)}")
    return keys_by_kid


async def _get_jwk_for_kid(kid: str) -> Optional[dict]:
    """Найти JWK по kid. Refresh кеша если истёк ИЛИ kid отсутствует.

    Single retry on kid-miss — защита от rotation, но без бесконечного цикла
    если злоумышленник шлёт мусорный kid.
    """
    global _JWKS_LOCK
    if _JWKS_LOCK is None:
        _JWKS_LOCK = asyncio.Lock()

    keys = _JWKS_CACHE["keys_by_kid"]
    fresh = (_time.time() - _JWKS_CACHE["fetched_at"]) < _JWKS_TTL_SECONDS

    if fresh and kid in keys:
        return keys[kid]

    async with _JWKS_LOCK:
        # double-check после получения лока (другой запрос мог обновить)
        keys = _JWKS_CACHE["keys_by_kid"]
        fresh = (_time.time() - _JWKS_CACHE["fetched_at"]) < _JWKS_TTL_SECONDS
        if fresh and kid in keys:
            return keys[kid]
        try:
            keys = await _fetch_jwks()
        except Exception as e:
            logger.error(f"[auth] JWKS fetch failed: {e!r}")
            # fall through — keys остался старый, и/или пустой; вернём None если kid не найден
        return keys.get(kid)


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

    Branch 1: Authorization: Bearer <JWT> — validate Supabase ES256 (JWKS) or HS256 fallback
    Branch 2: X-Workspace-Key: wsk_...    — bcrypt verify against workspace_api_keys
    No credentials → 401 AUTH_REQUIRED
    """
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
        claims = await _decode_supabase_jwt(token)
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

async def _decode_supabase_jwt(token: str) -> dict:
    """
    Decode + verify Supabase JWT (ES256 via JWKS, HS256 fallback for legacy).

    Routing off the JWT `alg` header:
      - ES256 → fetch JWK by `kid` from project's JWKS, verify EC P-256 signature
      - HS256 → verify against settings.supabase_jwt_secret (legacy projects)
      - other → 401 TOKEN_INVALID

    Raises HTTPException(401) for expired, invalid claims, or signature errors.
    """
    # Read unverified header to route on algorithm.
    try:
        header = jwt.get_unverified_header(token)
    except JWTError:
        raise HTTPException(
            status_code=401,
            detail={"code": "TOKEN_INVALID", "message": "Invalid JWT (malformed header)"},
        )

    alg = header.get("alg")
    kid = header.get("kid")

    if alg == "ES256":
        if not kid:
            raise HTTPException(
                status_code=401,
                detail={"code": "TOKEN_INVALID", "message": "Invalid JWT (missing kid for ES256)"},
            )
        jwk = await _get_jwk_for_kid(kid)
        if jwk is None:
            logger.warning(f"[auth] no JWK found for kid={kid}")
            raise HTTPException(
                status_code=401,
                detail={"code": "TOKEN_INVALID", "message": "Invalid JWT (unknown kid)"},
            )
        key = jwk
        algorithms = ["ES256"]
    elif alg == "HS256":
        if not settings.supabase_jwt_secret:
            raise HTTPException(
                status_code=401,
                detail={"code": "TOKEN_INVALID", "message": "Invalid JWT (HS256 not configured)"},
            )
        key = settings.supabase_jwt_secret
        algorithms = ["HS256"]
    else:
        raise HTTPException(
            status_code=401,
            detail={"code": "TOKEN_INVALID", "message": f"Invalid JWT (unsupported alg={alg!r})"},
        )

    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=algorithms,
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
    """Verify wsk_<...> token. Phase 02.1 (CR-09) hardening.

    - **issue 1 (timing)**: prefix sring-equality через ``hmac.compare_digest``
      (constant-time defence-in-depth поверх SQL prefix-фильтра).
    - **issue 2 (CPU)**: in-process LRU cache 5-min TTL — повторный вызов с
      тем же raw_token не делает bcrypt.
    - **issue 3 (anti-pattern)**: ``last_used_at = datetime.now(timezone.utc)``
      вместо ``func.now()`` (Python attribute, не SQL-expression).
    """
    if len(raw_token) < 12:
        raise HTTPException(
            status_code=401,
            detail={"code": "API_KEY_INVALID", "message": "Malformed workspace key"},
        )

    # ─── Fast path: cache hit ─────────────────────────────────────────────
    cached_ctx = _cache_get(raw_token)
    if cached_ctx is not None:
        return cached_ctx

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
        # CR-09 issue 1: constant-time prefix string-equality. SQL уже
        # отфильтровал по `prefix = :prefix`, но повторяем compare_digest
        # для защиты-в-глубину против side-channel в Python.
        if not hmac.compare_digest(prefix.encode(), candidate.prefix.encode()):
            continue
        # Pitfall 3: bcrypt sync — обернуть в to_thread
        match = await asyncio.to_thread(
            bcrypt.checkpw,
            raw_token.encode(),
            candidate.bcrypt_hash.encode(),
        )
        if match:
            # CR-09 issue 3: clean Python datetime, не SQL func.now()
            candidate.last_used_at = datetime.now(timezone.utc)
            await db.commit()

            ctx = AuthCtx(
                workspace_id=candidate.workspace_id,
                user_id=None,
                source="api_key",
                role=None,
            )
            # CR-09 issue 2: populate cache (5-min TTL)
            _cache_put(raw_token, ctx)

            logger.info(
                f"[auth] api_key matched workspace={candidate.workspace_id} "
                f"prefix={prefix} key_id={str(candidate.id)[:8]}..."
            )
            return ctx

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
