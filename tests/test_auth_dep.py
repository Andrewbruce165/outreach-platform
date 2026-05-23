"""
Tests для app/utils/auth.py auth_dep (AUTH-02, AUTH-03, TENT-02).

Direct unit-style: вызываем auth_dep через фикстуру async_db_session,
без HTTP-уровня (тот покрыт в test_workspace_router.py плана 01-03).
"""

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import UserWorkspace, Workspace, WorkspaceApiKey
from app.utils.auth import (
    AuthCtx,
    _decode_supabase_jwt,
    _resolve_or_create_workspace,
    _verify_api_key,
)


# ─── JWT decode tests (AUTH-03) ──────────────────────────────────────────────


async def test_decode_valid_jwt(valid_supabase_jwt):
    """Валидный JWT → claims dict с sub и email."""
    token = valid_supabase_jwt(sub="user-123", email="user@example.com")
    claims = await _decode_supabase_jwt(token)
    assert claims["sub"] == "user-123"
    assert claims["email"] == "user@example.com"


async def test_decode_expired_jwt(expired_supabase_jwt):
    """Истёкший JWT → 401 TOKEN_EXPIRED."""
    with pytest.raises(HTTPException) as exc:
        await _decode_supabase_jwt(expired_supabase_jwt)
    assert exc.value.status_code == 401
    assert exc.value.detail["code"] == "TOKEN_EXPIRED"


async def test_decode_invalid_jwt():
    """Bogus token → 401 TOKEN_INVALID."""
    with pytest.raises(HTTPException) as exc:
        await _decode_supabase_jwt("not-a-real-jwt")
    assert exc.value.status_code == 401
    assert exc.value.detail["code"] == "TOKEN_INVALID"


async def test_decode_jwt_wrong_audience(valid_supabase_jwt):
    """JWT с aud != 'authenticated' → 401."""
    token = valid_supabase_jwt(aud="anon")
    with pytest.raises(HTTPException) as exc:
        await _decode_supabase_jwt(token)
    assert exc.value.status_code == 401


# ─── ES256 JWKS path (Phase 05.1-DEBUG 2026-05-23) ───────────────────────────


async def test_decode_es256_jwt_via_jwks(es256_supabase_jwt):
    """ES256 JWT с kid из подготовленного JWKS → claims dict (JWKS path)."""
    token = es256_supabase_jwt(sub="es256-user-001", email="es@example.com")
    claims = await _decode_supabase_jwt(token)
    assert claims["sub"] == "es256-user-001"
    assert claims["email"] == "es@example.com"


async def test_decode_es256_jwt_unknown_kid(es256_supabase_jwt_unknown_kid):
    """ES256 JWT с kid которого нет в JWKS (и refetch не помог) → 401 TOKEN_INVALID."""
    with pytest.raises(HTTPException) as exc:
        await _decode_supabase_jwt(es256_supabase_jwt_unknown_kid)
    assert exc.value.status_code == 401
    assert exc.value.detail["code"] == "TOKEN_INVALID"


async def test_decode_jwt_unsupported_alg(unsupported_alg_jwt):
    """JWT с alg=none или другим неподдерживаемым → 401 TOKEN_INVALID."""
    with pytest.raises(HTTPException) as exc:
        await _decode_supabase_jwt(unsupported_alg_jwt)
    assert exc.value.status_code == 401
    assert exc.value.detail["code"] == "TOKEN_INVALID"


# ─── Lazy workspace create (TENT-02, D-08, Pitfall 5) ────────────────────────


async def test_lazy_workspace_create_with_email(async_db_session: AsyncSession):
    """Первый JWT-запрос с новым sub + email → создание workspace с name=email."""
    sub = "new-user-uuid-001"
    email = "newuser@example.com"

    ctx = await _resolve_or_create_workspace(
        async_db_session, supabase_user_id=sub, email=email
    )

    assert isinstance(ctx, AuthCtx)
    assert ctx.source == "jwt"
    assert ctx.user_id == sub
    assert ctx.role == "owner"

    result = await async_db_session.execute(
        select(Workspace).where(Workspace.id == ctx.workspace_id)
    )
    workspace = result.scalars().first()
    assert workspace is not None
    assert workspace.name == email


async def test_lazy_workspace_create_without_email(async_db_session: AsyncSession):
    """Если email отсутствует — name workspace = 'My Workspace' (D-09)."""
    sub = "new-user-uuid-002"

    ctx = await _resolve_or_create_workspace(
        async_db_session, supabase_user_id=sub, email=None
    )

    result = await async_db_session.execute(
        select(Workspace).where(Workspace.id == ctx.workspace_id)
    )
    workspace = result.scalars().first()
    assert workspace.name == "My Workspace"


async def test_repeated_request_finds_existing(async_db_session: AsyncSession):
    """Повторный запрос с тем же sub → тот же workspace_id, без дубликата.

    W-1 защита: между двумя вызовами _resolve_or_create_workspace явный flush()
    гарантирует видимость INSERT'а для последующего SELECT (защита от identity
    map / autoflush — без flush в редких race условиях SELECT может не увидеть
    свежевставленный row).
    """
    sub = "returning-user-uuid"
    email = "returning@example.com"

    ctx1 = await _resolve_or_create_workspace(async_db_session, sub, email)
    # W-1: явный flush гарантирует видимость INSERT для последующего SELECT
    await async_db_session.flush()
    ctx2 = await _resolve_or_create_workspace(async_db_session, sub, email)

    assert ctx1.workspace_id == ctx2.workspace_id

    from sqlalchemy import text
    count_result = await async_db_session.execute(
        text("SELECT COUNT(*) FROM user_workspaces WHERE supabase_user_id = :s"),
        {"s": sub},
    )
    assert count_result.scalar() == 1

    result = await async_db_session.execute(
        select(UserWorkspace).where(UserWorkspace.supabase_user_id == sub)
    )
    rows = result.scalars().all()
    assert len(rows) == 1


# ─── API key flow (TENT-03 — read side) ─────────────────────────────────────


async def test_verify_api_key_invalid_format(async_db_session: AsyncSession):
    """Ключ без префикса wsk_ → не должен попасть в _verify_api_key (auth_dep level)."""
    with pytest.raises(HTTPException) as exc:
        await _verify_api_key(async_db_session, "wsk_short")  # < 12 chars
    assert exc.value.status_code == 401


async def test_verify_api_key_valid_match(async_db_session: AsyncSession):
    """Валидный wsk_ ключ → AuthCtx(source='api_key')."""
    import asyncio
    import secrets

    import bcrypt

    workspace = Workspace(name="API Test WS")
    async_db_session.add(workspace)
    await async_db_session.flush()

    raw_secret = secrets.token_urlsafe(32)
    full_token = f"wsk_{raw_secret}"
    prefix = full_token[:12]
    hash_bytes = await asyncio.to_thread(
        bcrypt.hashpw, full_token.encode(), bcrypt.gensalt(rounds=4)
    )

    api_key = WorkspaceApiKey(
        workspace_id=workspace.id,
        prefix=prefix,
        bcrypt_hash=hash_bytes.decode(),
        name="test-key",
    )
    async_db_session.add(api_key)
    await async_db_session.commit()

    ctx = await _verify_api_key(async_db_session, full_token)

    assert ctx.workspace_id == workspace.id
    assert ctx.source == "api_key"
    assert ctx.user_id is None
    assert ctx.role is None


async def test_verify_revoked_api_key(async_db_session: AsyncSession):
    """Revoked ключ (revoked_at IS NOT NULL) → 401 API_KEY_INVALID."""
    import asyncio
    import secrets
    from datetime import datetime, timezone

    import bcrypt

    workspace = Workspace(name="Revoked WS")
    async_db_session.add(workspace)
    await async_db_session.flush()

    raw_secret = secrets.token_urlsafe(32)
    full_token = f"wsk_{raw_secret}"
    prefix = full_token[:12]
    hash_bytes = await asyncio.to_thread(
        bcrypt.hashpw, full_token.encode(), bcrypt.gensalt(rounds=4)
    )

    api_key = WorkspaceApiKey(
        workspace_id=workspace.id,
        prefix=prefix,
        bcrypt_hash=hash_bytes.decode(),
        name="revoked-key",
        revoked_at=datetime.now(timezone.utc),
    )
    async_db_session.add(api_key)
    await async_db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await _verify_api_key(async_db_session, full_token)
    assert exc.value.status_code == 401
    assert exc.value.detail["code"] == "API_KEY_INVALID"
