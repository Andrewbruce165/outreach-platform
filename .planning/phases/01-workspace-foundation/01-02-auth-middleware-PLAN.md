---
phase: 01-workspace-foundation
plan: 02
type: execute
wave: 2
depends_on:
  - "01-01"
files_modified:
  - requirements.txt
  - pyproject.toml
  - tests/__init__.py
  - tests/conftest.py
  - tests/test_migration_012.py
  - tests/test_auth_dep.py
  - app/utils/auth.py
autonomous: true
requirements:
  - AUTH-02
  - AUTH-03
  - TENT-02
user_setup:
  - service: supabase
    why: "Validate Supabase HS256 JWTs locally (D-05)"
    env_vars:
      - name: SUPABASE_JWT_SECRET
        source: "Supabase Dashboard → Project Settings → API → JWT Settings → legacy JWT Secret. Если поле отсутствует — переключиться в legacy mode (см. Pitfall 1 в RESEARCH)"
      - name: SUPABASE_URL
        source: "Supabase Dashboard → Project Settings → API → Project URL"
    dashboard_config: []
must_haves:
  truths:
    - "Pytest-инфраструктура с нуля установлена: pytest + pytest-asyncio + httpx работают"
    - "auth_dep валидирует Supabase HS256 JWT через python-jose с audience='authenticated'"
    - "auth_dep валидирует X-Workspace-Key через bcrypt.checkpw в asyncio.to_thread (Pitfall 3 — event loop не блокируется)"
    - "Валидный JWT без записи user_workspaces — создаёт workspace + user_workspaces атомарно (D-08, Pitfall 5)"
    - "Возвращаемый AuthCtx(workspace_id, user_id, source, role) типизирован Pydantic-моделью"
    - "Запрос без заголовков → 401 AUTH_REQUIRED"
    - "Запрос с истёкшим JWT → 401 TOKEN_EXPIRED; невалидный → 401 TOKEN_INVALID"
    - "Запрос с revoked api-key → 401 API_KEY_INVALID"
    - "Все pytest-тесты для миграции 012 + auth_dep зелёные"
  artifacts:
    - path: "tests/conftest.py"
      provides: "Фикстуры async_db_session, async_client, valid_supabase_jwt"
      exports: ["async_db_session", "async_client", "valid_supabase_jwt"]
    - path: "tests/test_migration_012.py"
      provides: "Параметризованный smoke: каждая из 11 таблиц имеет NOT NULL workspace_id"
      contains: "TENANT_SCOPED_TABLES"
    - path: "tests/test_auth_dep.py"
      provides: "Integration tests: no-auth, valid-jwt, expired-jwt, invalid-jwt, lazy-create, valid-api-key, revoked-api-key"
      contains: "test_lazy_workspace_create"
    - path: "app/utils/auth.py"
      provides: "AuthCtx Pydantic model + auth_dep FastAPI Depends + private helpers"
      exports: ["AuthCtx", "auth_dep"]
    - path: "pyproject.toml"
      provides: "Pytest конфиг с asyncio_mode='auto'"
      contains: "[tool.pytest.ini_options]"
    - path: "requirements.txt"
      provides: "bcrypt + pytest + pytest-asyncio добавлены"
      contains: "bcrypt"
  key_links:
    - from: "app/utils/auth.py:auth_dep"
      to: "app/models.UserWorkspace + Workspace"
      via: "SELECT user_workspaces WHERE supabase_user_id; INSERT workspace + user_workspaces в одной транзакции (Pitfall 5)"
      pattern: "async with db\\.begin\\(\\)"
    - from: "app/utils/auth.py:_verify_api_key"
      to: "app/models.WorkspaceApiKey"
      via: "SELECT prefix WHERE revoked_at IS NULL; await asyncio.to_thread(bcrypt.checkpw, ...) — Pitfall 3"
      pattern: "asyncio\\.to_thread.*bcrypt"
    - from: "tests/test_auth_dep.py"
      to: "app/utils/auth.py:auth_dep"
      via: "httpx.AsyncClient (ASGITransport) + valid_supabase_jwt фикстура с тестовым SUPABASE_JWT_SECRET"
      pattern: "AsyncClient.*ASGITransport"
---

<objective>
План создаёт auth-фундамент Phase 1: pytest-инфраструктуру с нуля (Wave 0 — её в репо вообще нет), модуль `app/utils/auth.py` с `AuthCtx` Pydantic-моделью и `auth_dep` FastAPI Depends, который ветвится по заголовку (Bearer JWT vs X-Workspace-Key) и выполняет lazy auto-create workspace при первом JWT-входе. Тесты покрывают миграцию 012 (TENT-01 smoke по 11 таблицам) и auth_dep (AUTH-03 + TENT-02).

Покрытие requirement-ов:
- **AUTH-02** — переход magic link → JWT-сессия: FastAPI принимает выданный Supabase JWT, декодит HS256 через `python-jose`, извлекает `sub` + `email`.
- **AUTH-03** — верификация Supabase JWT: `_decode_supabase_jwt` с `algorithms=['HS256']`, `audience='authenticated'`, `options={'require':['sub','exp']}` (Pitfall 1 — legacy JWT Secret).
- **TENT-02** — workspace создаётся автоматически при первом входе: `_resolve_or_create_workspace` lazy-create в одной транзакции `async with db.begin()` (D-08, Pitfall 5 — race condition).

Purpose: Без auth_dep плана 01-03 (workspace router) — некуда подключать `ctx: AuthCtx = Depends(auth_dep)`. Этот план — критический мостик между БД и API-уровнем.
Output: Pytest-инфраструктура, файл auth.py, два test-модуля (миграция + auth_dep), обновлённый requirements.txt + новый pyproject.toml.
</objective>

<execution_context>
@/Users/andrewbruce/Documents/outreach-platform/CLAUDE.md
@/Users/andrewbruce/Documents/outreach-platform/.planning/codebase/CONVENTIONS.md
@/Users/andrewbruce/Documents/outreach-platform/.planning/codebase/STRUCTURE.md
</execution_context>

<context>
@/Users/andrewbruce/Documents/outreach-platform/.planning/PROJECT.md
@/Users/andrewbruce/Documents/outreach-platform/.planning/ROADMAP.md
@/Users/andrewbruce/Documents/outreach-platform/.planning/STATE.md
@/Users/andrewbruce/Documents/outreach-platform/.planning/REQUIREMENTS.md
@/Users/andrewbruce/Documents/outreach-platform/.planning/phases/01-workspace-foundation/01-CONTEXT.md
@/Users/andrewbruce/Documents/outreach-platform/.planning/phases/01-workspace-foundation/01-RESEARCH.md
@/Users/andrewbruce/Documents/outreach-platform/.planning/phases/01-workspace-foundation/01-PATTERNS.md
@/Users/andrewbruce/Documents/outreach-platform/.planning/phases/01-workspace-foundation/01-01-SUMMARY.md

# Канонические аналоги
@/Users/andrewbruce/Documents/outreach-platform/app/routers/auth.py
@/Users/andrewbruce/Documents/outreach-platform/app/routers/senders.py
@/Users/andrewbruce/Documents/outreach-platform/app/database.py
@/Users/andrewbruce/Documents/outreach-platform/app/config.py
@/Users/andrewbruce/Documents/outreach-platform/app/models/__init__.py
@/Users/andrewbruce/Documents/outreach-platform/requirements.txt

<interfaces>
<!-- AuthCtx Pydantic v2 модель (D-12 + RESEARCH §Code Examples 2): -->
class AuthCtx(BaseModel):
    workspace_id: UUID
    user_id: Optional[str]           # supabase 'sub' для JWT, None для API key
    source: Literal["jwt", "api_key"]
    role: Optional[str]              # 'owner'/'admin'/'member' для JWT, None для API key

<!-- Сигнатура auth_dep (для использования в плане 01-03): -->
async def auth_dep(
    authorization: Optional[str] = Header(None),
    x_workspace_key: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
) -> AuthCtx

<!-- Канонический паттерн JWT decode (RESEARCH §Code Examples 1): -->
claims = jwt.decode(
    token,
    settings.supabase_jwt_secret,
    algorithms=["HS256"],
    audience="authenticated",
    options={"require": ["sub", "exp"]},
)
# Исключения: ExpiredSignatureError, JWTClaimsError, JWTError

<!-- 11 tenant-scoped таблиц (для test_migration_012): -->
TENANT_SCOPED_TABLES = [
    "senders", "messages_log", "contacts_cache", "ai_contexts",
    "message_queue", "conversations", "warmup_pool", "warmup_sessions",
    "warmup_messages", "proxy_pool", "context_contact_assignments",
]
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Wave 0 — установить pytest-инфраструктуру + добавить bcrypt</name>
  <files>requirements.txt, pyproject.toml, tests/__init__.py, tests/conftest.py</files>
  <read_first>
    - /Users/andrewbruce/Documents/outreach-platform/requirements.txt (текущий формат — `pkg==version`, секции `# Section`)
    - /Users/andrewbruce/Documents/outreach-platform/CLAUDE.md ("async everywhere" — все тесты async)
    - /Users/andrewbruce/Documents/outreach-platform/.planning/phases/01-workspace-foundation/01-RESEARCH.md (§Validation Architecture; §Standard Stack для bcrypt версии; Pitfall 3 — bcrypt sync; §Code Examples — JWT factory pattern)
    - /Users/andrewbruce/Documents/outreach-platform/.planning/phases/01-workspace-foundation/01-PATTERNS.md (§10-13 — нет аналогов тестов в репо, шаблоны Pattern A/B/C из RESEARCH)
    - /Users/andrewbruce/Documents/outreach-platform/.planning/codebase/STRUCTURE.md (нет директории tests/, создаём с нуля)
    - /Users/andrewbruce/Documents/outreach-platform/app/database.py (get_db, AsyncSessionLocal, init_db — для фикстур)
    - /Users/andrewbruce/Documents/outreach-platform/app/main.py (FastAPI app — для ASGITransport)
  </read_first>
  <action>
**Часть A — обновить `requirements.txt`**

Добавить в секцию `# Security` (после `python-jose`):
```
bcrypt>=4.1.0,<5.0
```

Добавить новую секцию в конец файла:
```
# Testing (Phase 1 Wave 0)
pytest>=8.0
pytest-asyncio>=0.23
```

`httpx` уже есть (==0.26.0) — не дублировать.

**НЕ удалять** `alembic==1.13.1` (legacy, не используется, но удаление не в скоупе Phase 1).

**Часть B — создать `pyproject.toml`** в корне проекта со следующим содержимым:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
python_files = ["test_*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
addopts = "-v --tb=short --strict-markers"
markers = [
    "integration: integration tests touching the database",
]
```

Если `pyproject.toml` уже существует — Read его, добавить секцию `[tool.pytest.ini_options]` без удаления остального содержимого.

**Часть C — создать `tests/__init__.py`** — пустой файл (только знак для Python, что это package).

**Часть D — создать `tests/conftest.py`** с фикстурами:

```python
"""
Pytest fixtures для Phase 1 (auth_dep + workspace router тесты).

Стратегия:
- async_db_session: каждый тест в своей транзакции, rollback на teardown — изоляция.
- async_client: httpx.AsyncClient + ASGITransport(app) — in-process FastAPI без сети.
- valid_supabase_jwt: фабрика, генерирует HS256 JWT с тестовым SUPABASE_JWT_SECRET.
"""

import logging
from typing import AsyncGenerator, Callable

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import AsyncSessionLocal, init_db, engine, Base
from app.main import app

logger = logging.getLogger(__name__)


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _setup_database():
    """Создаёт схему перед всеми тестами и применяет миграцию 012."""
    import os
    from sqlalchemy import text

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # apply migration 012 (raw SQL)
        migration_path = os.path.join(
            os.path.dirname(__file__), "..", "migrations", "012_workspace.sql"
        )
        with open(migration_path) as f:
            sql = f.read()
        # Удаляем BEGIN/COMMIT — engine.begin() уже даёт транзакцию
        sql_no_tx = sql.replace("BEGIN;", "").replace("COMMIT;", "")
        for statement in sql_no_tx.split(";"):
            stmt = statement.strip()
            if stmt:
                await conn.execute(text(stmt))

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
```

**Критичные конвенции (из PATTERNS.md):**
- `snake_case` имена файлов
- `async def` функции
- `pytest_asyncio.fixture` декоратор для async фикстур (НЕ `@pytest.fixture`)
- Импорты — stdlib, third-party, app, разделители blank line (CONVENTIONS.md §Import Organization)

**Важно:** `_setup_database` — session-scope autouse. Это гарантирует что миграция 012 применилась один раз на запуск тестов. На уровне теста используем `AsyncSessionLocal` + rollback для изоляции.

**Что НЕ делать:**
- НЕ использовать `@pytest.fixture` для async — только `@pytest_asyncio.fixture`.
- НЕ создавать отдельный test database в Phase 1 — используем тот же docker-compose `db` (D-01 — БД пустая). Тесты ожидают что разработчик запустил `docker compose up -d db`.
- НЕ добавлять `pytest-postgresql` (упомянут в RESEARCH §Validation Architecture как опция) — minimum viable infra на Phase 1.
- НЕ ставить `requirements-dev.txt` отдельно — добавляем в основной requirements.txt (RESEARCH допускает оба, выбираем единый файл для простоты).
  </action>
  <verify>
    <automated>
test -f requirements.txt && \
grep -qE "^bcrypt>=4\.1" requirements.txt && \
grep -qE "^pytest>=8" requirements.txt && \
grep -qE "^pytest-asyncio>=0\.23" requirements.txt && \
test -f pyproject.toml && \
grep -q "asyncio_mode" pyproject.toml && \
grep -q "tool.pytest.ini_options" pyproject.toml && \
test -f tests/__init__.py && \
test -f tests/conftest.py && \
grep -q "async_db_session" tests/conftest.py && \
grep -q "async_client" tests/conftest.py && \
grep -q "valid_supabase_jwt" tests/conftest.py && \
grep -q "ASGITransport" tests/conftest.py && \
grep -q "pytest_asyncio.fixture" tests/conftest.py
    </automated>
  </verify>
  <acceptance_criteria>
- `requirements.txt` содержит строку `bcrypt>=4.1.0,<5.0` (НЕ `passlib`, НЕ `bcrypt==X` точное — диапазон)
- `requirements.txt` содержит `pytest>=8.0` и `pytest-asyncio>=0.23`
- `pyproject.toml` существует и содержит `[tool.pytest.ini_options]` секцию с `asyncio_mode = "auto"` и `testpaths = ["tests"]`
- `tests/__init__.py` существует (пустой или с docstring)
- `tests/conftest.py` существует и содержит:
  - `async_db_session` фикстура (с rollback в finally)
  - `async_client` фикстура (использует `ASGITransport(app=app)`)
  - `valid_supabase_jwt` фикстура (генерирует HS256 JWT через `jose.jwt.encode`)
  - `_setup_database` session-scope autouse фикстура с применением миграции 012
- Все async фикстуры декорированы `@pytest_asyncio.fixture` (НЕ `@pytest.fixture`)
- Импорт `from app.main import app` присутствует
- НЕТ упоминания `pytest-postgresql` или `requirements-dev.txt` (minimum viable infra)
  </acceptance_criteria>
  <done>
Pytest-инфраструктура установлена с нуля; bcrypt доступен для импорта; фикстуры готовы для тестов миграции и auth_dep в Task 3-4.
  </done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: Создать app/utils/auth.py — AuthCtx + auth_dep (dual-auth)</name>
  <files>app/utils/auth.py</files>
  <behavior>
    Тесты (определяются в Task 4, но контракт фиксируется здесь):
    - test_no_auth_rejected: GET без заголовков → 401, detail.code == "AUTH_REQUIRED"
    - test_decode_valid_jwt: валидный JWT → auth_dep возвращает AuthCtx(workspace_id=UUID, user_id=sub, source="jwt", role="owner")
    - test_expired_jwt: JWT с exp=1 → 401, detail.code == "TOKEN_EXPIRED"
    - test_invalid_jwt: bogus token → 401, detail.code == "TOKEN_INVALID"
    - test_lazy_workspace_create: первый запрос с валидным JWT (нет записи в user_workspaces) → создаётся Workspace (name=email) + UserWorkspace (role="owner") атомарно, AuthCtx содержит новый workspace_id
    - test_existing_user_workspace: повторный запрос → lookup, тот же workspace_id
    - test_valid_api_key: X-Workspace-Key с активным ключом → AuthCtx(workspace_id, user_id=None, source="api_key", role=None); last_used_at обновляется
    - test_revoked_api_key: X-Workspace-Key с revoked_at IS NOT NULL → 401, detail.code == "API_KEY_INVALID"
    - test_malformed_api_key: ключ не начинается с "wsk_" → 401
    - test_both_headers_jwt_wins: оба заголовка → JWT branch использован (порядок в коде)
  </behavior>
  <read_first>
    - /Users/andrewbruce/Documents/outreach-platform/app/routers/auth.py (текущий verify_api_key — структура замены; будет удалён в плане 01-03)
    - /Users/andrewbruce/Documents/outreach-platform/app/routers/senders.py (lines 1-11 — паттерн импортов для router/dep файла; line 54-64 — паттерн async DB execute)
    - /Users/andrewbruce/Documents/outreach-platform/app/database.py (get_db, AsyncSessionLocal — паттерн DB injection)
    - /Users/andrewbruce/Documents/outreach-platform/app/config.py (Settings класс — добавить SUPABASE_JWT_SECRET / SUPABASE_URL здесь? НЕТ — это в плане 01-03. Здесь auth.py читает уже-добавленные поля)
    - /Users/andrewbruce/Documents/outreach-platform/app/models/__init__.py (после плана 01-01 — содержит Workspace, UserWorkspace, WorkspaceApiKey)
    - /Users/andrewbruce/Documents/outreach-platform/.planning/phases/01-workspace-foundation/01-RESEARCH.md (§Code Examples 1, 2 — точные сигнатуры; Pitfalls 1, 3, 5, 6; §Architecture Patterns Pattern 2)
    - /Users/andrewbruce/Documents/outreach-platform/.planning/phases/01-workspace-foundation/01-PATTERNS.md (§3 — структура файла, импорты, Pydantic v2 паттерн)
    - /Users/andrewbruce/Documents/outreach-platform/.planning/phases/01-workspace-foundation/01-CONTEXT.md (D-05, D-06, D-08, D-11, D-12, D-13)
    - /Users/andrewbruce/Documents/outreach-platform/.planning/codebase/CONVENTIONS.md (§Error Handling — dict detail с code+message; §Logging — logger через __name__)
  </read_first>
  <action>
**ВНИМАНИЕ:** Этот task зависит от того, что `app/config.py` уже содержит `supabase_jwt_secret: str` (это будет добавлено в плане 01-03 Task 1, но 01-03 в той же Wave 2). Чтобы избежать порядка-зависимости в Wave 2 — этот task **сам добавит обязательные поля в config.py**, если их нет. Это исключение из принципа exclusive ownership ради минимизации wave-depth.

**Часть A — pre-flight: проверить и добавить minimum в app/config.py** (defensive):

Прочитать `app/config.py`. Если поле `supabase_jwt_secret: str` ОТСУТСТВУЕТ — добавить в `Settings` класс:

```python
    # Supabase (Phase 1)
    supabase_jwt_secret: str
```

(Полная конфигурация — `supabase_url`, `cors_allowed_origins`, удаление `api_key` — это Task 1 плана 01-03. Здесь — минимум для работы auth.py.)

Если поле ALREADY есть (план 01-03 завершён раньше) — не трогать config.py, перейти к части B.

**Часть B — создать `app/utils/auth.py`** со следующей структурой (~150 строк):

```python
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
```

**Критичные конвенции и пятна:**
- `logger = logging.getLogger(__name__)` + `logger.info/warning` — НЕ `print()` (CLAUDE.md)
- `from app.config import get_settings; settings = get_settings()` — синглтон через lru_cache (CONVENTIONS.md §Module Design)
- HTTPException с `detail` как dict с `code`+`message` (CONVENTIONS.md §Error Handling)
- Эмодзи **НЕ использовать в логах** auth.py — это security-sensitive, чистые логи (listener.py использует эмодзи, но это user-facing visual scanning, не наш случай)
- НИКОГДА не логировать `raw_token` целиком — только `prefix` (12 символов). НИКОГДА не логировать JWT-токен.
- Логировать только первые 8 символов `supabase_user_id` (Convention §Logging — truncated IDs).
- **Pitfall 3** — `bcrypt.checkpw` обязательно в `asyncio.to_thread`.
- **Pitfall 5** — atomic transaction + post-commit SELECT.
- **TODO-маркеры** для v2-rls, v2-pyjwt, v2-es256 — обязательны (D-04 говорит "оставить TODO" + RESEARCH Pitfalls 1, 2).

**Что НЕ делать:**
- НЕ использовать `dependencies=[Depends(auth_dep)]` на уровне APIRouter — теряем ctx в хендлерах (см. PATTERNS.md §3 Anti-pattern).
- НЕ создавать раздельные `jwt_only_dep` и `api_key_only_dep` — нарушает D-11.
- НЕ кэшировать lookup в памяти (D-07 — никакого кэша в v1).
- НЕ делать `HTTP-вызов в Supabase` — local validation only (D-05).
- НЕ удалять старый `app/routers/auth.py` здесь — это в плане 01-03 Task 2.
- НЕ модифицировать `app/main.py` — это в плане 01-03 Task 3.
  </action>
  <verify>
    <automated>
test -f app/utils/auth.py && \
grep -q "class AuthCtx" app/utils/auth.py && \
grep -q "async def auth_dep" app/utils/auth.py && \
grep -q "_decode_supabase_jwt" app/utils/auth.py && \
grep -q "_resolve_or_create_workspace" app/utils/auth.py && \
grep -q "_verify_api_key" app/utils/auth.py && \
grep -q "asyncio.to_thread" app/utils/auth.py && \
grep -q "algorithms=\[\"HS256\"\]" app/utils/auth.py && \
grep -q 'audience="authenticated"' app/utils/auth.py && \
grep -q "AUTH_REQUIRED" app/utils/auth.py && \
grep -q "TOKEN_EXPIRED" app/utils/auth.py && \
grep -q "API_KEY_INVALID" app/utils/auth.py && \
grep -q "async with db.begin" app/utils/auth.py && \
grep -q "TODO(v2" app/utils/auth.py && \
! grep -q "print(" app/utils/auth.py && \
python3 -c "from app.utils.auth import auth_dep, AuthCtx; from uuid import UUID; ctx = AuthCtx(workspace_id=UUID('00000000-0000-0000-0000-000000000001'), user_id='x', source='jwt', role='owner'); assert ctx.source == 'jwt'"
    </automated>
  </verify>
  <acceptance_criteria>
- `app/utils/auth.py` существует и Python-импортируется без ошибок
- Содержит `class AuthCtx(BaseModel)` с полями `workspace_id: UUID`, `user_id: Optional[str]`, `source: Literal["jwt", "api_key"]`, `role: Optional[str]` (D-12)
- Содержит `async def auth_dep(authorization, x_workspace_key, db) -> AuthCtx`
- Содержит приватные хелперы `_decode_supabase_jwt`, `_resolve_or_create_workspace`, `_verify_api_key`
- JWT decode использует `algorithms=["HS256"]`, `audience="authenticated"`, `options={"require": ["sub", "exp"]}` (D-05, AUTH-03)
- bcrypt вызывается через `asyncio.to_thread(bcrypt.checkpw, ...)` — обязательно для Pitfall 3
- Lazy workspace create использует `async with db.begin():` для атомарности (Pitfall 5, D-08)
- Post-commit re-SELECT присутствует (защита от race condition)
- HTTPException detail имеет формат `{"code": "...", "message": "..."}` (CONVENTIONS.md)
- Коды ошибок присутствуют: `AUTH_REQUIRED`, `TOKEN_EXPIRED`, `TOKEN_INVALID`, `TOKEN_INVALID_CLAIMS`, `API_KEY_INVALID`
- TODO-маркеры в коде: `TODO(v2)` минимум 2 раза (для PyJWT и ES256 migration); `TODO(v2-rls)` минимум 2 раза (для RLS migration)
- НЕТ `print(` в файле; используется `logger = logging.getLogger(__name__)`
- НЕ логируется полный `raw_token` или `token` — только prefix (`prefix={prefix}` или `supabase_user_id[:8]`)
- `app/config.py` содержит `supabase_jwt_secret: str` (defensive added если ещё не было)
- Импортируется в Python: `python3 -c "from app.utils.auth import auth_dep, AuthCtx"` exit 0
  </acceptance_criteria>
  <done>
Файл `app/utils/auth.py` реализует dual-auth с lazy workspace create; покрывает AUTH-02, AUTH-03, TENT-02; готов к использованию в роутерах плана 01-03.
  </done>
</task>

<task type="auto">
  <name>Task 3: Тесты — миграция 012 + auth_dep (test_migration_012.py + test_auth_dep.py)</name>
  <files>tests/test_migration_012.py, tests/test_auth_dep.py</files>
  <read_first>
    - /Users/andrewbruce/Documents/outreach-platform/tests/conftest.py (фикстуры из Task 1 — `async_db_session`, `async_client`, `valid_supabase_jwt`, `expired_supabase_jwt`)
    - /Users/andrewbruce/Documents/outreach-platform/app/utils/auth.py (Task 2 — точные сигнатуры для assert)
    - /Users/andrewbruce/Documents/outreach-platform/.planning/phases/01-workspace-foundation/01-RESEARCH.md (§Validation Architecture — Pattern B/C schema test и endpoint integration test)
    - /Users/andrewbruce/Documents/outreach-platform/.planning/phases/01-workspace-foundation/01-PATTERNS.md (§10-13 — no analog, шаблоны из RESEARCH)
    - /Users/andrewbruce/Documents/outreach-platform/app/models/__init__.py (Workspace, UserWorkspace для прямых INSERT в тестах)
  </read_first>
  <action>
**Часть A — создать `tests/test_migration_012.py`**

Параметризованный тест по 11 таблицам — каждая должна иметь NOT NULL workspace_id FK (TENT-01).

```python
"""
Tests для миграции 012_workspace.sql.

Покрывает TENT-01: все 11 tenant-scoped таблиц получили NOT NULL workspace_id UUID FK.

Стратегия:
- _setup_database fixture (conftest.py, session-scope) применила миграцию.
- Здесь читаем information_schema.columns и information_schema.table_constraints,
  проверяем что схема соответствует ожидаемой.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


TENANT_SCOPED_TABLES = [
    "senders",
    "messages_log",
    "contacts_cache",
    "ai_contexts",
    "message_queue",
    "conversations",
    "warmup_pool",
    "warmup_sessions",
    "warmup_messages",
    "proxy_pool",
    "context_contact_assignments",
]

NEW_TABLES = ["workspaces", "user_workspaces", "workspace_api_keys"]


@pytest.mark.parametrize("table", TENANT_SCOPED_TABLES)
async def test_workspace_id_column_exists_not_null(
    async_db_session: AsyncSession, table: str
):
    """Каждая из 11 tenant-scoped таблиц должна иметь NOT NULL UUID workspace_id."""
    result = await async_db_session.execute(
        text(
            """
            SELECT data_type, is_nullable
            FROM information_schema.columns
            WHERE table_name = :t AND column_name = 'workspace_id'
            """
        ),
        {"t": table},
    )
    row = result.fetchone()
    assert row is not None, f"{table}: column 'workspace_id' is missing"
    assert row[0] == "uuid", f"{table}: workspace_id type is {row[0]}, expected uuid"
    assert row[1] == "NO", f"{table}: workspace_id is nullable, expected NOT NULL"


@pytest.mark.parametrize("table", TENANT_SCOPED_TABLES)
async def test_workspace_id_has_fk_cascade(
    async_db_session: AsyncSession, table: str
):
    """FK workspace_id → workspaces.id с ON DELETE CASCADE на каждой таблице."""
    result = await async_db_session.execute(
        text(
            """
            SELECT tc.constraint_name, rc.delete_rule
            FROM information_schema.table_constraints tc
            JOIN information_schema.referential_constraints rc
              ON tc.constraint_name = rc.constraint_name
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
            WHERE tc.table_name = :t
              AND tc.constraint_type = 'FOREIGN KEY'
              AND kcu.column_name = 'workspace_id'
            """
        ),
        {"t": table},
    )
    row = result.fetchone()
    assert row is not None, f"{table}: no FK constraint on workspace_id"
    assert row[1] == "CASCADE", f"{table}: delete_rule is {row[1]}, expected CASCADE"


@pytest.mark.parametrize("table", NEW_TABLES)
async def test_new_table_exists(async_db_session: AsyncSession, table: str):
    """workspaces / user_workspaces / workspace_api_keys существуют."""
    result = await async_db_session.execute(
        text(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_name = :t AND table_schema = 'public'
            """
        ),
        {"t": table},
    )
    assert result.fetchone() is not None, f"Table {table} missing after migration 012"


async def test_user_workspaces_role_check_constraint(async_db_session: AsyncSession):
    """user_workspaces.role имеет CHECK constraint (anti-pattern protection)."""
    result = await async_db_session.execute(
        text(
            """
            SELECT con.conname
            FROM pg_constraint con
            JOIN pg_class cls ON cls.oid = con.conrelid
            WHERE cls.relname = 'user_workspaces'
              AND con.contype = 'c'
              AND con.conname = 'user_workspaces_role_check'
            """
        )
    )
    assert result.fetchone() is not None, (
        "user_workspaces_role_check constraint missing"
    )


async def test_workspace_api_keys_partial_index(async_db_session: AsyncSession):
    """Partial индекс по prefix WHERE revoked_at IS NULL (C-02)."""
    result = await async_db_session.execute(
        text(
            """
            SELECT indexdef FROM pg_indexes
            WHERE tablename = 'workspace_api_keys'
              AND indexname = 'idx_workspace_api_keys_prefix_active'
            """
        )
    )
    row = result.fetchone()
    assert row is not None, "partial index on workspace_api_keys missing"
    assert "revoked_at IS NULL" in row[0], (
        f"index def missing WHERE clause: {row[0]}"
    )


async def test_no_unique_on_supabase_user_id(async_db_session: AsyncSession):
    """D-10: НЕТ UNIQUE constraint на user_workspaces.supabase_user_id."""
    result = await async_db_session.execute(
        text(
            """
            SELECT con.conname
            FROM pg_constraint con
            JOIN pg_class cls ON cls.oid = con.conrelid
            JOIN pg_attribute att ON att.attrelid = cls.oid
            WHERE cls.relname = 'user_workspaces'
              AND con.contype = 'u'
              AND att.attname = 'supabase_user_id'
              AND att.attnum = ANY(con.conkey)
            """
        )
    )
    assert result.fetchone() is None, (
        "D-10 violation: UNIQUE constraint on supabase_user_id (must be many-to-many)"
    )
```

**Часть B — создать `tests/test_auth_dep.py`**

Integration-тесты через `async_client` (с реальным auth_dep + базой через миграцию 012). Используется `POST /api/v1/auth/me` как тестовый endpoint — но этот endpoint появится в плане 01-03. На этом этапе он ещё не существует, поэтому в этом тесте мы:

1. Тестируем `auth_dep` напрямую через прямой вызов функции (unit-style), используя фикстуру `async_db_session`.
2. Часть integration-сценариев (через httpx → /api/v1/auth/me) останется как "skipped" или в test_workspace_router.py плана 01-03.

```python
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
from app.utils.auth import AuthCtx, _decode_supabase_jwt, _resolve_or_create_workspace, _verify_api_key, auth_dep


# ─── JWT decode tests (AUTH-03) ──────────────────────────────────────────────

def test_decode_valid_jwt(valid_supabase_jwt):
    """Валидный JWT → claims dict с sub и email."""
    token = valid_supabase_jwt(sub="user-123", email="user@example.com")
    claims = _decode_supabase_jwt(token)
    assert claims["sub"] == "user-123"
    assert claims["email"] == "user@example.com"


def test_decode_expired_jwt(expired_supabase_jwt):
    """Истёкший JWT → 401 TOKEN_EXPIRED."""
    with pytest.raises(HTTPException) as exc:
        _decode_supabase_jwt(expired_supabase_jwt)
    assert exc.value.status_code == 401
    assert exc.value.detail["code"] == "TOKEN_EXPIRED"


def test_decode_invalid_jwt():
    """Bogus token → 401 TOKEN_INVALID."""
    with pytest.raises(HTTPException) as exc:
        _decode_supabase_jwt("not-a-real-jwt")
    assert exc.value.status_code == 401
    assert exc.value.detail["code"] == "TOKEN_INVALID"


def test_decode_jwt_wrong_audience(valid_supabase_jwt):
    """JWT с aud != 'authenticated' → 401."""
    token = valid_supabase_jwt(aud="anon")
    with pytest.raises(HTTPException) as exc:
        _decode_supabase_jwt(token)
    assert exc.value.status_code == 401


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

    # Verify в БД создалась запись с правильным именем
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
    """Повторный запрос с тем же sub → тот же workspace_id, без дубликата."""
    sub = "returning-user-uuid"
    email = "returning@example.com"

    ctx1 = await _resolve_or_create_workspace(async_db_session, sub, email)
    ctx2 = await _resolve_or_create_workspace(async_db_session, sub, email)

    assert ctx1.workspace_id == ctx2.workspace_id

    # В БД только одна запись user_workspaces
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

    # Создаём workspace и api-key вручную через ORM
    workspace = Workspace(name="API Test WS")
    async_db_session.add(workspace)
    await async_db_session.flush()

    raw_secret = secrets.token_urlsafe(32)
    full_token = f"wsk_{raw_secret}"
    prefix = full_token[:12]
    hash_bytes = await asyncio.to_thread(
        bcrypt.hashpw, full_token.encode(), bcrypt.gensalt(rounds=4)  # low cost для теста
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
        revoked_at=datetime.now(timezone.utc),  # revoked!
    )
    async_db_session.add(api_key)
    await async_db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await _verify_api_key(async_db_session, full_token)
    assert exc.value.status_code == 401
    assert exc.value.detail["code"] == "API_KEY_INVALID"
```

**Критично:**
- `pytest.mark.parametrize` для покрытия 11 таблиц одной тест-функцией.
- Использование фикстур `async_db_session`, `valid_supabase_jwt`, `expired_supabase_jwt` из conftest.py.
- bcrypt с `rounds=4` в тестах — минимум для скорости (production использует default ~12).
- НЕ использовать httpx.AsyncClient в этом плане — endpoint `/api/v1/auth/me` ещё не существует. Integration-тесты через HTTP — в плане 01-03.

**Что НЕ делать:**
- НЕ менять conftest.py — он создан в Task 1.
- НЕ тестировать workspace router endpoints — это плана 01-03.
- НЕ использовать manual SQL INSERTs где можно использовать ORM.
  </action>
  <verify>
    <automated>
test -f tests/test_migration_012.py && \
test -f tests/test_auth_dep.py && \
grep -q "TENANT_SCOPED_TABLES" tests/test_migration_012.py && \
[ "$(grep -c '@pytest.mark.parametrize' tests/test_migration_012.py)" -ge "3" ] && \
grep -q "user_workspaces_role_check" tests/test_migration_012.py && \
grep -q "revoked_at IS NULL" tests/test_migration_012.py && \
grep -q "test_no_unique_on_supabase_user_id" tests/test_migration_012.py && \
grep -q "test_lazy_workspace_create_with_email" tests/test_auth_dep.py && \
grep -q "test_lazy_workspace_create_without_email" tests/test_auth_dep.py && \
grep -q "test_repeated_request_finds_existing" tests/test_auth_dep.py && \
grep -q "test_decode_expired_jwt" tests/test_auth_dep.py && \
grep -q "test_verify_revoked_api_key" tests/test_auth_dep.py && \
grep -q "test_verify_api_key_valid_match" tests/test_auth_dep.py && \
docker compose up -d db && sleep 3 && \
pytest tests/test_migration_012.py tests/test_auth_dep.py -v --tb=short 2>&1
    </automated>
  </verify>
  <acceptance_criteria>
- `tests/test_migration_012.py` существует, содержит:
  - `TENANT_SCOPED_TABLES` список из 11 имён
  - `test_workspace_id_column_exists_not_null` (параметризованный по 11 таблицам)
  - `test_workspace_id_has_fk_cascade` (параметризованный, проверка ON DELETE CASCADE)
  - `test_new_table_exists` (параметризованный по 3 новым таблицам)
  - `test_user_workspaces_role_check_constraint`
  - `test_workspace_api_keys_partial_index` (проверяет `WHERE revoked_at IS NULL` в indexdef)
  - `test_no_unique_on_supabase_user_id` (D-10 enforcement)
- `tests/test_auth_dep.py` существует, содержит минимум 9 тест-функций:
  - 4 JWT decode тестов: `test_decode_valid_jwt`, `test_decode_expired_jwt`, `test_decode_invalid_jwt`, `test_decode_jwt_wrong_audience`
  - 3 lazy create тестов: `test_lazy_workspace_create_with_email`, `test_lazy_workspace_create_without_email`, `test_repeated_request_finds_existing`
  - 3+ api key тестов: `test_verify_api_key_invalid_format`, `test_verify_api_key_valid_match`, `test_verify_revoked_api_key`
- Все тесты используют фикстуры из conftest.py
- Прогон `pytest tests/test_migration_012.py tests/test_auth_dep.py -v` exit 0 (все тесты зелёные)
- bcrypt в тестах использует `rounds=4` (НЕ дефолт) для скорости — `grep "rounds=4" tests/test_auth_dep.py` non-empty
  </acceptance_criteria>
  <done>
Pytest зелёный по: миграция 012 покрывает 11 таблиц + 3 новые + CHECK + partial index + D-10; auth_dep тесты покрывают AUTH-03 (4 JWT сценария), TENT-02 (lazy create), TENT-03 read-side (api key match + revoke).
  </done>
</task>

</tasks>

<verification>
**Phase-уровневая верификация после всех 3 задач плана:**

1. Зависимости установлены:
   ```bash
   docker compose exec api pip list | grep -E "(bcrypt|pytest)"
   # bcrypt, pytest, pytest-asyncio — все три присутствуют
   ```

2. Импорт работает:
   ```bash
   python3 -c "from app.utils.auth import auth_dep, AuthCtx; print('OK')"
   ```

3. Все тесты Phase 1 проходят:
   ```bash
   docker compose up -d db
   pytest tests/test_migration_012.py tests/test_auth_dep.py -v
   # все тесты зелёные
   ```

4. Static-проверки:
   - `grep -c "TODO(v2" app/utils/auth.py` ≥ 3
   - `grep "print(" app/utils/auth.py` пусто

**Известное ограничение:** этот план НЕ покрывает HTTP-level integration test через `async_client.get("/api/v1/workspace")` — endpoint появится в плане 01-03. Там же будут добавлены `test_workspace_router.py` и `test_workspace_api_keys.py`.
</verification>

<success_criteria>
- [ ] Pytest, pytest-asyncio, bcrypt в requirements.txt
- [ ] pyproject.toml содержит pytest config с asyncio_mode='auto'
- [ ] tests/conftest.py содержит async_db_session, async_client, valid_supabase_jwt, expired_supabase_jwt фикстуры
- [ ] app/utils/auth.py реализует auth_dep (dual-auth), AuthCtx, _decode_supabase_jwt, _resolve_or_create_workspace, _verify_api_key
- [ ] HS256 + audience='authenticated' (D-05)
- [ ] bcrypt.checkpw обёрнут в asyncio.to_thread (Pitfall 3)
- [ ] Lazy workspace create в `async with db.begin()` + post-commit SELECT (Pitfall 5)
- [ ] TODO-маркеры для v2-pyjwt, v2-es256, v2-rls
- [ ] Никаких print(), JWT/токены в логах
- [ ] test_migration_012.py + test_auth_dep.py зелёные (pytest exit 0)
</success_criteria>

<output>
После завершения создать файл `.planning/phases/01-workspace-foundation/01-02-SUMMARY.md` с описанием:
- Что создано (pyproject.toml, tests/, app/utils/auth.py)
- Какие фикстуры pytest доступны для последующих планов
- Сигнатура auth_dep и AuthCtx для импорта в роутерах
- Какие тесты зелёные, что отложено в 01-03
- Готовность к плану 01-03 (workspace router + cleanup main.py)
</output>
