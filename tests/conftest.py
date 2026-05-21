"""pytest-фикстуры для outreach-platform."""

import os

# Выставляем env vars ДО любого импорта app.* — иначе pydantic Settings
# упадёт с ValidationError на module-level get_settings() в app/utils/auth.py.
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-for-pytest-only-do-not-use-in-prod")
os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "http://localhost:3000")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/outreach_test")

import logging
from typing import AsyncGenerator, Callable

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession

# Только теперь — импорты app:
from app.config import get_settings  # noqa: E402
from app.database import AsyncSessionLocal, engine, Base  # noqa: E402
from app.main import app  # noqa: E402

logger = logging.getLogger(__name__)


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _setup_database():
    """Создаёт схему перед всеми тестами и применяет миграцию 012."""
    import pathlib

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # Применяем миграцию целиком через exec_driver_sql (не split по ";" —
        # partial-индексы WHERE revoked_at IS NULL и CHECK с запятыми ломают наивный сплиттер).
        # BEGIN/COMMIT в миграции уже есть, но run_sync даёт нам autocommit-engine для setup,
        # поэтому оставляем как есть — Postgres выполнит транзакцию как одну statement-batch.
        PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
        sql_012 = (PROJECT_ROOT / "migrations" / "012_workspace.sql").read_text()
        await conn.exec_driver_sql(sql_012)

        # Phase 2 migration: folders, contacts, onboarding_sessions, csv_imports
        # + senders extension (lifecycle_status, rate_per_*, role CHECK) - is_active.
        sql_013 = (PROJECT_ROOT / "migrations" / "013_phase2.sql").read_text()
        await conn.exec_driver_sql(sql_013)

    yield

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def async_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Изолированная DB-сессия с rollback после теста."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.rollback()
            await session.close()


@pytest_asyncio.fixture
async def async_client() -> AsyncGenerator[AsyncClient, None]:
    """httpx.AsyncClient с in-process ASGITransport — без реальной сети."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
def valid_supabase_jwt() -> Callable[..., str]:
    """Фабрика валидных HS256 JWT для тестов auth_dep."""
    settings = get_settings()

    def _factory(
        sub: str = "test-user-uuid-default",
        email: str | None = "test@example.com",
        exp: int = 9999999999,  # 2286 год
        aud: str = "authenticated",
    ) -> str:
        claims = {"sub": sub, "email": email, "aud": aud, "exp": exp}
        return jwt.encode(claims, settings.supabase_jwt_secret, algorithm="HS256")

    return _factory


@pytest_asyncio.fixture
def expired_supabase_jwt(valid_supabase_jwt: Callable[..., str]) -> str:
    """Истёкший JWT для теста TOKEN_EXPIRED."""
    return valid_supabase_jwt(exp=1)  # 1970-01-01
