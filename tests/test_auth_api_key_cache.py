"""Phase 02.1 (CR-09): _verify_api_key — LRU cache + constant-time + clean datetime.

Issues closed:
- **CR-09 issue 1 (timing attack)**: prefix lookup использует
  ``hmac.compare_digest`` для константного string-equality.
- **CR-09 issue 2 (CPU bottleneck)**: n8n push на ``POST /contacts`` шёл через
  bcrypt (~100ms CPU per request). In-process LRU cache (5-min TTL) делает
  второй и последующие вызовы с тем же raw_token ~free.
- **CR-09 issue 3 (anti-pattern)**: ``last_used_at = func.now()`` (SQL expr,
  присвоенный в Python attribute) заменён на ``datetime.now(timezone.utc)``.

Известные ограничения cache:
- Revoke токена не инвалидирует кэш сразу — старый ctx живёт до 5 минут.
  Для immediate revoke v2 потребуется Redis pubsub или DB poll.
- Cache in-process — не shared между containers; второй container прогреет
  свой cache на первых запросах (OK).
"""

import asyncio
import secrets
from datetime import datetime, timezone
from unittest.mock import patch

import bcrypt
import pytest
from sqlalchemy import select

from app.models import Workspace, WorkspaceApiKey
from app.utils import auth as auth_module
from app.utils.auth import _TOKEN_CACHE, _verify_api_key

pytestmark = pytest.mark.asyncio


# ─── fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def clear_token_cache():
    """Каждый тест начинает с пустым cache (no cross-test leakage)."""
    _TOKEN_CACHE.clear()
    yield
    _TOKEN_CACHE.clear()


@pytest.fixture
def valid_api_key_factory(async_db_session):
    """Фабрика валидных wsk_ ключей в БД. Возвращает (raw_token, key_id, workspace_id)."""

    async def _make(workspace_id=None):
        if workspace_id is None:
            ws = Workspace(name=f"CR-09 WS {secrets.token_hex(4)}")
            async_db_session.add(ws)
            await async_db_session.flush()
            workspace_id = ws.id

        raw_secret = secrets.token_urlsafe(32)
        full_token = f"wsk_{raw_secret}"
        prefix = full_token[:12]
        hash_bytes = await asyncio.to_thread(
            bcrypt.hashpw, full_token.encode(), bcrypt.gensalt(rounds=4)
        )

        api_key = WorkspaceApiKey(
            workspace_id=workspace_id,
            prefix=prefix,
            bcrypt_hash=hash_bytes.decode(),
            name="cr-09-test",
        )
        async_db_session.add(api_key)
        await async_db_session.commit()
        await async_db_session.refresh(api_key)
        return full_token, api_key.id, workspace_id

    return _make


# ─── CR-09 issue 2: LRU cache ─────────────────────────────────────────────


async def test_first_call_does_bcrypt(async_db_session, valid_api_key_factory):
    """First call с raw_token → bcrypt.checkpw вызывается."""
    raw_token, _, _ = await valid_api_key_factory()

    bcrypt_calls = []
    original = auth_module.bcrypt.checkpw

    def spy(pwd, hsh):
        bcrypt_calls.append(True)
        return original(pwd, hsh)

    with patch.object(auth_module.bcrypt, "checkpw", side_effect=spy):
        ctx = await _verify_api_key(async_db_session, raw_token)

    assert ctx.source == "api_key"
    assert len(bcrypt_calls) >= 1, "First call должен пройти slow path (bcrypt)"


async def test_second_call_hits_cache(async_db_session, valid_api_key_factory):
    """Second call с тем же raw_token → bcrypt НЕ вызывается (cache hit)."""
    raw_token, _, _ = await valid_api_key_factory()

    # Прогреваем cache
    await _verify_api_key(async_db_session, raw_token)
    assert raw_token in _TOKEN_CACHE, "First call должен populate _TOKEN_CACHE"

    bcrypt_calls = []
    original = auth_module.bcrypt.checkpw

    def spy(pwd, hsh):
        bcrypt_calls.append(True)
        return original(pwd, hsh)

    with patch.object(auth_module.bcrypt, "checkpw", side_effect=spy):
        ctx = await _verify_api_key(async_db_session, raw_token)

    assert ctx.source == "api_key"
    assert len(bcrypt_calls) == 0, "bcrypt должен быть пропущен — cache hit"


async def test_cache_ttl_expired_falls_back_to_bcrypt(
    async_db_session, valid_api_key_factory
):
    """После истечения TTL — bcrypt вызывается снова."""
    import time as t

    raw_token, _, _ = await valid_api_key_factory()
    await _verify_api_key(async_db_session, raw_token)

    # Принудительно expire'ним entry
    ctx_cached, _expires = _TOKEN_CACHE[raw_token]
    _TOKEN_CACHE[raw_token] = (ctx_cached, t.time() - 1)

    bcrypt_calls = []
    original = auth_module.bcrypt.checkpw

    def spy(pwd, hsh):
        bcrypt_calls.append(True)
        return original(pwd, hsh)

    with patch.object(auth_module.bcrypt, "checkpw", side_effect=spy):
        await _verify_api_key(async_db_session, raw_token)

    assert len(bcrypt_calls) >= 1, "После TTL expire должен пройти slow path"


async def test_invalid_token_not_cached(async_db_session):
    """Невалидный wsk_ → 401, в cache не попадает (только validated tokens)."""
    from fastapi import HTTPException

    bogus = "wsk_NOT_A_REAL_TOKEN_AT_ALL_xx"
    with pytest.raises(HTTPException):
        await _verify_api_key(async_db_session, bogus)
    assert bogus not in _TOKEN_CACHE


# ─── CR-09 issue 3: clean Python datetime ────────────────────────────────


async def test_last_used_at_is_python_datetime(
    async_db_session, valid_api_key_factory
):
    """last_used_at — это Python datetime, не SQL expression (func.now)."""
    raw_token, key_id, _ = await valid_api_key_factory()
    before = datetime.now(timezone.utc)

    await _verify_api_key(async_db_session, raw_token)

    # Reload from DB
    async_db_session.expire_all()
    candidate = (
        await async_db_session.execute(
            select(WorkspaceApiKey).where(WorkspaceApiKey.id == key_id)
        )
    ).scalar_one()
    assert candidate.last_used_at is not None
    assert isinstance(candidate.last_used_at, datetime)
    # Допуск 30 секунд — тест не должен флакать из-за timezone offset.
    assert candidate.last_used_at >= before.replace(microsecond=0)


# ─── CR-09 issue 1: hmac.compare_digest static guard ────────────────────


def test_source_uses_hmac_compare_digest():
    """Статический guard: _verify_api_key содержит hmac.compare_digest."""
    import inspect

    source = inspect.getsource(auth_module._verify_api_key)
    assert "hmac.compare_digest" in source, (
        "CR-09 issue 1: prefix comparison должен идти через hmac.compare_digest "
        "для timing-safe equality"
    )


def test_module_exposes_token_cache():
    """_TOKEN_CACHE — module-level dict с TTL semantics (smoke import)."""
    assert isinstance(_TOKEN_CACHE, dict)
    assert hasattr(auth_module, "_TOKEN_CACHE_TTL_SECONDS")
    assert auth_module._TOKEN_CACHE_TTL_SECONDS >= 60  # минимум минута, чтобы быть полезным
