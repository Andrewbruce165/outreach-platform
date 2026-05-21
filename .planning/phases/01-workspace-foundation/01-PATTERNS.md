# Phase 1: Workspace Foundation — Pattern Map

**Mapped:** 2026-05-21
**Files analyzed:** 13 (создаются или модифицируются)
**Analogs found:** 9 / 13 (4 теста — без аналогов в репо, тестов в проекте нет вообще)

---

## File Classification

| Файл (New/Modified) | Роль | Data Flow | Аналог в репо | Качество совпадения |
|---|---|---|---|---|
| `migrations/012_workspace.sql` (NEW) | migration (raw SQL) | DDL / transactional | `migrations/005_warmup.sql` + `migrations/010_missing_indexes.sql` + `migrations/011_sender_auth_status.sql` | exact (role + flow) |
| `app/models/__init__.py` (MOD) | ORM model definitions | declarative schema | сам файл (Sender, AIContext, ProxyPool — extend in place) | exact (self-analog) |
| `app/utils/auth.py` (NEW) | FastAPI Depends (auth) | request-response middleware | `app/routers/auth.py` (текущий `verify_api_key`) | role-match (только auth) — паттерн заменяется, а не копируется |
| `app/routers/workspace.py` (NEW) | router (CRUD) | request-response | `app/routers/contexts.py` | exact (role + flow: CRUD по одному ресурсу с UUID PK) |
| `app/main.py` (MOD) | app entry / router registration | startup | сам файл | exact (in-place edit) |
| `app/config.py` (MOD) | pydantic-settings | config | сам файл | exact (in-place edit) |
| `app/database.py` (MOD, C-04 optional) | DB engine + session factory | infrastructure | сам файл | exact (in-place edit) |
| `docker-compose.yml` (MOD) | container orchestration | infrastructure | сам файл | exact (in-place edit) |
| `requirements.txt` (MOD) | deps | config | сам файл | exact (in-place edit) |
| `tests/conftest.py` (NEW) | test fixtures | infrastructure | **нет** — тестов в проекте нет | no analog (используем RESEARCH.md §Validation Architecture) |
| `tests/test_migration_012.py` (NEW) | unit (DB schema) | DB introspection | **нет** | no analog |
| `tests/test_auth_dep.py` (NEW) | integration (auth) | request-response | **нет** | no analog |
| `tests/test_workspace_router.py` (NEW) | integration (router) | request-response | **нет** | no analog |

---

## Pattern Assignments

### 1. `migrations/012_workspace.sql` (NEW) — raw SQL migration

**Аналоги:**
- `migrations/005_warmup.sql` — паттерн `CREATE TABLE IF NOT EXISTS` + UUID PK + FK ON DELETE CASCADE + индексы в одной транзакции.
- `migrations/011_sender_auth_status.sql` — паттерн `ALTER TABLE ... ADD COLUMN IF NOT EXISTS ... VARCHAR(N) NOT NULL DEFAULT 'x'`.
- `migrations/010_missing_indexes.sql` — паттерн `CREATE INDEX IF NOT EXISTS ... WHERE <partial>`.
- `migrations/003_add_sender_role.sql` — паттерн `VARCHAR(20) NOT NULL DEFAULT '<value>'` для role-like полей (использовать вместо SQLEnum-типа, см. CONCERNS.md).

**Pattern A — обрамление транзакцией** (из `005_warmup.sql:3,50`):
```sql
-- migrations/005_warmup.sql
-- Таблицы для системы прогрева аккаунтов
BEGIN;
-- ... все DDL внутри ...
COMMIT;
```

**Pattern B — CREATE TABLE IF NOT EXISTS с UUID PK и TIMESTAMPTZ** (из `005_warmup.sql:6-12`):
```sql
CREATE TABLE IF NOT EXISTS warmup_pool (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sender_id   UUID NOT NULL REFERENCES senders(id) ON DELETE CASCADE,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    enrolled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(sender_id)
);
```
Применить для `workspaces`, `user_workspaces`, `workspace_api_keys`. **Не использовать `uuid_generate_v4()`** — это extension, в репо везде `gen_random_uuid()`.

**Pattern C — VARCHAR + CHECK для enum-подобного поля** (из `005_warmup.sql:20` + комментарий + `003_add_sender_role.sql:5-6`):
```sql
-- 005_warmup.sql line 20:
status          VARCHAR(20) NOT NULL DEFAULT 'active',   -- active, completed

-- 003_add_sender_role.sql lines 5-6:
ALTER TABLE senders
ADD COLUMN IF NOT EXISTS role VARCHAR(20) NOT NULL DEFAULT 'sender';
```
**Anti-pattern для копирования:** `senders.role` в 003 **не имеет CHECK constraint** (anti-pattern, см. CONCERNS.md). Для нового `user_workspaces.role` **добавить CHECK** явно:
```sql
role VARCHAR(20) NOT NULL DEFAULT 'owner',
CONSTRAINT user_workspaces_role_check CHECK (role IN ('owner','admin','member'))
```

**Pattern D — ALTER TABLE ADD COLUMN IF NOT EXISTS** (из `011_sender_auth_status.sql:5-7`):
```sql
ALTER TABLE senders
    ADD COLUMN IF NOT EXISTS auth_status VARCHAR(30) NOT NULL DEFAULT 'ok';
```
Применить для добавления `workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE` на 11 tenant-scoped таблицах. БД пустая (D-01) — `NOT NULL` без `DEFAULT` безопасен.

**Pattern E — CREATE INDEX IF NOT EXISTS, включая partial** (из `010_missing_indexes.sql:9-11, 33-35`):
```sql
-- composite + partial index:
CREATE INDEX IF NOT EXISTS idx_message_queue_sender_status_scheduled
    ON message_queue(sender_id, status, scheduled_at)
    WHERE status IN ('pending', 'processing');

-- partial index "only active rows":
CREATE INDEX IF NOT EXISTS idx_proxy_pool_assigned_sender
    ON proxy_pool(assigned_to_sender_id)
    WHERE assigned_to_sender_id IS NOT NULL;
```
Применить для `workspace_api_keys(prefix) WHERE revoked_at IS NULL` (партиал — только активные) и для базовых `idx_<table>_workspace ON <table>(workspace_id)` на 11 таблицах.

**Naming convention для индексов:** `idx_<table>_<columns>` или `idx_<table>_<purpose>` (lowercase, snake_case). Все примеры в `010_missing_indexes.sql` следуют этому правилу.

**Tenant-scoped tables, которые нужно ALTER (см. RESEARCH §Tenant-Scoped Tables Inventory):**
`senders`, `messages_log`, `contacts_cache`, `ai_contexts`, `message_queue`, `conversations`, `warmup_pool`, `warmup_sessions`, `warmup_messages`, `proxy_pool`, `context_contact_assignments`.

---

### 2. `app/models/__init__.py` (MOD) — добавить 3 модели + workspace_id Column на 11 моделей

**Аналог:** сам файл — расширение существующего барреля.

**Pattern A — заголовок импортов** (из `app/models/__init__.py:1-7`):
```python
from sqlalchemy import Column, String, Text, Boolean, BigInteger, DateTime, Integer, ForeignKey, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base
import uuid
import enum
```
**Не плодить новых импортов** — всё уже есть, кроме `Text` (если планер захочет хранить bcrypt-hash как Text — он уже импортирован).

**Pattern B — каноничная модель с UUID PK и timestamps** (из `app/models/__init__.py:29-48`, `Sender`):
```python
class Sender(Base):
    __tablename__ = "senders"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug = Column(String(50), unique=True, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    phone = Column(String(20), nullable=False)
    session_string = Column(Text, nullable=False)
    is_active = Column(Boolean, default=True, server_default='true')
    role = Column(String(20), nullable=False, server_default='sender')
    proxy = Column(JSONB, nullable=True)
    auth_status = Column(String(30), nullable=False, server_default='ok')
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_used_at = Column(DateTime(timezone=True), onupdate=func.now())
```
Точные конвенции:
- `id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)` — UUID PK с Python-default.
- `created_at = Column(DateTime(timezone=True), server_default=func.now())` — server-side timestamp.
- `updated_at = Column(..., server_default=func.now(), onupdate=func.now())` — auto-update (см. `AIContext:103`).
- Для VARCHAR + default: `String(N), nullable=False, server_default='value'` — **строковый литерал в server_default**, не Python value.

**Pattern C — модель с relationship и FK CASCADE** (из `app/models/__init__.py:179-189`, `WarmupPool`):
```python
class WarmupPool(Base):
    __tablename__ = "warmup_pool"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sender_id   = Column(UUID(as_uuid=True), ForeignKey("senders.id", ondelete="CASCADE"),
                         nullable=False, unique=True)
    is_active   = Column(Boolean, nullable=False, default=True, server_default='true')
    enrolled_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    sender = relationship("Sender")
```
Применить для `UserWorkspace.workspace_id` и `WorkspaceApiKey.workspace_id` (оба с `ondelete="CASCADE"` соответственно D-06/D-13).

**Pattern D — модель с updated_at** (из `app/models/__init__.py:87-106`, `AIContext`):
```python
class AIContext(Base):
    __tablename__ = "ai_contexts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False)
    # ...
    is_active = Column(Boolean, default=True, server_default='true')
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
```
Шаблон для `Workspace` (нужны `name`, `created_at`, `updated_at`).

**Anti-pattern, который НЕ повторять:** `Sender.role = Column(String(20), ..., server_default='sender')` **без enum-валидации на Python-уровне** (CONCERNS.md). Для `UserWorkspace.role` рекомендация — оставить `String(20)` (SQL-уровень покрыт CHECK в миграции), но в Pydantic-схемах роутера применить `Literal['owner','admin','member']` для валидации входа.

**Расширение существующих 11 моделей:** в каждую — одна строка, после `id` и до бизнес-полей (если есть `sender_id` рядом, разместить `workspace_id` сразу после `id`):
```python
workspace_id = Column(UUID(as_uuid=True),
                     ForeignKey("workspaces.id", ondelete="CASCADE"),
                     nullable=False)
```

**Important:** Согласно landmine Pitfall 4 (RESEARCH.md): миграция и ORM **должны быть синхронны**. Любая ALTER в 012 — соответствующая колонка в `__init__.py`.

---

### 3. `app/utils/auth.py` (NEW) — dual-auth FastAPI Depends

**Аналог (структурный):** `app/routers/auth.py` (текущий `verify_api_key`, 25 строк) — паттерн single-Depends с `HTTPException(401)`. Полностью заменяется новым кодом. Старый файл удаляется (см. RESEARCH §"Open Questions §4").

**Аналог (импорты и async-стиль):** любой роутер с DB-сессией, например `app/routers/senders.py:1-11` + `app/routers/contexts.py:1-12`.

**Pattern A — заголовок импортов для router/dep файла** (из `app/routers/senders.py:1-11`):
```python
import logging
import subprocess
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models import Sender, AIContext
from app.schemas import SenderCreate, SenderUpdate, SenderResponse, SenderListResponse
from app.services.encryption import encrypt_session
from app.routers.auth import verify_api_key

logger = logging.getLogger(__name__)
```
Конвенция: stdlib → third-party → `app.*`. Группы разделены blank-line. `logger = logging.getLogger(__name__)` — стандартный паттерн логирования (см. CONVENTIONS.md §Logging).

**Pattern B — текущий verify_api_key** (полный файл `app/routers/auth.py:1-25`):
```python
from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader
from app.config import get_settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(api_key: str = Security(api_key_header)) -> str:
    """Verify API key from header."""
    settings = get_settings()

    if not api_key:
        raise HTTPException(
            status_code=401,
            detail="API key is missing. Provide X-API-Key header."
        )

    if api_key != settings.api_key:
        raise HTTPException(
            status_code=403,
            detail="Invalid API key"
        )

    return api_key
```
**Заменяется** на `async def auth_dep(...) -> AuthCtx`. Конвенции, которые повторяем:
- `async def` (CLAUDE.md "async everywhere").
- `HTTPException(401, detail=...)` — но **обновить формат detail на `{"code": "...", "message": "..."}`** (см. CONVENTIONS.md §Error Handling: "All HTTPException details use dicts with code and message keys when structured").
- Возврат типизированный (но вместо `str` теперь `AuthCtx` BaseModel).

**Pattern C — async DB-операция через `Depends(get_db)`** (из `app/routers/senders.py:53-64`):
```python
@router.get("", response_model=SenderListResponse)
async def list_senders(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key)
):
    """List all senders."""
    from sqlalchemy.orm import selectinload
    result = await db.execute(
        select(Sender).options(selectinload(Sender.ai_context)).order_by(Sender.name)
    )
    senders = result.scalars().all()
```
Применить в `_resolve_or_create_workspace` и `_verify_api_key` хелперах: `db: AsyncSession = Depends(get_db)`, `select(Model).where(...)`, `result.scalars().first()`.

**Pattern D — Pydantic v2 BaseModel** (из `app/routers/contexts.py:17-58`):
```python
class ContextCreate(BaseModel):
    name: str
    system_prompt: Optional[str] = None
    # ...

class ContextResponse(BaseModel):
    id: UUID
    name: str
    # ...
    created_at: datetime
    updated_at: datetime
```
**Без `model_config = ConfigDict(...)`** — в репо barebones BaseModel везде. Применить для `AuthCtx`:
```python
class AuthCtx(BaseModel):
    workspace_id: UUID
    user_id: Optional[str]
    source: Literal["jwt", "api_key"]
    role: Optional[str]
```
`Literal` — pydantic v2 поддерживает напрямую, дополнительной валидации не нужно.

**Конвенция приватных хелперов** (из CONVENTIONS.md §Naming Patterns): standalone private helpers prefixed with `_` — `_decode_supabase_jwt`, `_resolve_or_create_workspace`, `_verify_api_key` (последнее имя НЕ конфликтует с удаляемым `app.routers.auth.verify_api_key`, т.к. функция приватна и в другом модуле).

**Landmine — bcrypt sync** (Pitfall 3 RESEARCH.md): `bcrypt.checkpw` синхронный, оборачивать в `await asyncio.to_thread(...)`. В существующем коде есть аналог — `Fernet` в `app/services/encryption.py` тоже sync, но он CPU-cheap (`~1ms`), поэтому не обёрнут. Bcrypt — `~80ms`, обернуть обязательно.

---

### 4. `app/routers/workspace.py` (NEW) — CRUD-роутер для workspace + api-keys

**Аналог:** `app/routers/contexts.py` (260 строк) — наиболее близкий по структуре: CRUD по одному ресурсу с UUID PK, без relationships-зависимостей, inline schemas внутри router-файла.

**Pattern A — заголовок и определение роутера** (из `app/routers/contexts.py:1-13`):
```python
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
```
Применить:
```python
router = APIRouter(prefix="/api/v1/workspace", tags=["workspace"])
```
Импорт меняется на `from app.utils.auth import auth_dep, AuthCtx`. URL prefix — singular `/workspace` (см. RESEARCH §Endpoint Skeleton; routes plural как правило, но `workspace` — это «мой» единственный ресурс текущего auth-контекста, не коллекция).

**Pattern B — inline Pydantic schemas в router-файле** (из `app/routers/contexts.py:17-63`):
```python
# === Schemas ===
class ContextCreate(BaseModel):
    name: str
    system_prompt: Optional[str] = None
    # ...

class ContextUpdate(BaseModel):
    name: Optional[str] = None
    # ...

class ContextResponse(BaseModel):
    id: UUID
    name: str
    # ...
    created_at: datetime
    updated_at: datetime
```
**Замечание:** В CONVENTIONS.md §Module Design указано что схемы централизованы в `app/schemas/__init__.py`. Но `contexts.py` нарушает это правило и держит схемы локально с комментарием `# === Schemas ===`. Это де-факто принятый паттерн для роутеров с собственными схемами. **Решение для workspace.py:** держать `WorkspaceResponse`, `WorkspaceUpdate`, `ApiKeyCreateRequest`, `ApiKeyCreateResponse`, `ApiKeyListResponse` локально (контексты не shared) — повторяя паттерн contexts.py.

**Pattern C — CRUD endpoint с inline raw SQL и dependency-injection** (из `app/routers/contexts.py:67-95`):
```python
@router.get("", response_model=ContextListResponse)
async def list_contexts(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key)
):
    """Список всех AI контекстов (только активные)"""

    result = await db.execute(text("""
        SELECT id, name, ...
        FROM ai_contexts
        WHERE is_active = true
        ORDER BY created_at DESC
    """))
    rows = result.fetchall()

    contexts = [
        ContextResponse(
            id=row[0], name=row[1], ...
        )
        for row in rows
    ]

    return ContextListResponse(contexts=contexts, total=len(contexts))
```
**Применить в `workspace.py`:**
- Меняем `Depends(verify_api_key)` → `ctx: AuthCtx = Depends(auth_dep)`.
- `ctx.workspace_id` гарантированно UUID — используем в каждом `.where(workspace_id = :wid)`.
- `text(""" SELECT ... WHERE workspace_id = :wid """)` + `{"wid": str(ctx.workspace_id)}` — повторяя стиль raw SQL из contexts.py.
- **Альтернатива:** использовать ORM-объекты (`select(Workspace).where(Workspace.id == ctx.workspace_id)`) как в `senders.py:60-63`. **Рекомендация:** для workspace.py использовать ORM (новый код, тип-безопасность), хотя contexts.py — raw SQL. Этот выбор — discretion (C-01 уровень).

**Pattern D — POST endpoint, status 201, refresh после commit** (из `app/routers/senders.py:67-105`):
```python
@router.post("", response_model=SenderResponse, status_code=201)
async def create_sender(
    request: SenderCreate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key)
):
    """Add new sender."""
    # Check if slug exists
    existing = await db.execute(
        select(Sender).where(Sender.slug == request.slug)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"Sender with slug '{request.slug}' already exists")

    sender = Sender(slug=request.slug, ...)
    db.add(sender)
    await db.commit()
    await db.refresh(sender)
```
Применить для `POST /workspace/api-keys`: создание `WorkspaceApiKey` ORM-объекта → `db.add()` → `db.commit()` → `db.refresh()`. Возвращается `ApiKeyCreateResponse(token=full_token, ...)` — единственное место где plaintext-токен в ответе (D-13).

**Pattern E — PATCH endpoint с partial update** (из `app/routers/contexts.py:182-216`):
```python
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

    for field in ["name", "system_prompt", ...]:
        value = getattr(data, field)
        if value is not None:
            updates.append(f"{field} = :{field}")
            params[field] = value

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    updates.append("updated_at = NOW()")

    query = f"UPDATE ai_contexts SET {', '.join(updates)} WHERE id = :id"
    await db.execute(text(query), params)
    await db.commit()

    return await get_context(context_id, db, _)
```
Применить для `PATCH /workspace` (переименование). У `workspace` партиал: только `name`. Можно упростить — без цикла, просто `if data.name: workspace.name = data.name; await db.commit()`.

**Pattern F — DELETE endpoint с 204 No Content** (из `app/routers/contexts.py:219-259`):
```python
@router.delete("/{context_id}", status_code=204)
async def delete_context(
    context_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key)
):
    """..."""
    # Проверяем существование
    result = await db.execute(
        text("SELECT id FROM ai_contexts WHERE id = :id"),
        {"id": str(context_id)}
    )
    if not result.fetchone():
        raise HTTPException(status_code=404, detail="Context not found")

    # ... удаление ...
    await db.commit()
```
Применить для `DELETE /workspace/api-keys/{id}`. **Важно:** D-13 требует **soft-delete** (`revoked_at = NOW()`), а не `DELETE`. Поэтому SQL будет `UPDATE workspace_api_keys SET revoked_at = NOW() WHERE id = :id AND workspace_id = :wid` — с обязательным `AND workspace_id = ctx.workspace_id` для cross-tenant защиты (D-04 application-level enforcement).

**Anti-pattern, который НЕ копировать:** `_restart_listener` через `subprocess.run(["docker", "restart", ...])` в `senders.py:36-50` — явный anti-pattern (см. CONCERNS.md + код-контекст в CONTEXT.md). В workspace.py никаких подобных вызовов.

---

### 5. `app/main.py` (MOD) — register new router, drop old routers

**Аналог:** сам файл (in-place edit). Полный текущий вид виден выше в research-секции.

**Текущий импорт-блок** (`app/main.py:11-14`):
```python
from app.routers import send, senders, health, conversations, contexts, onboarding, check_contacts
from app.routers import queue as queue_router
from app.routers import warmup as warmup_router
from app.routers import proxy_pool as proxy_pool_router
```

**Текущая регистрация** (`app/main.py:66-75`):
```python
app.include_router(send.router)
app.include_router(senders.router)
app.include_router(health.router)
app.include_router(conversations.router)
app.include_router(contexts.router)
app.include_router(onboarding.router)
app.include_router(queue_router.router)
app.include_router(check_contacts.router)
app.include_router(warmup_router.router)
app.include_router(proxy_pool_router.router)
```

**После Phase 1 остаётся** (D-14: все 11 routers выпиливаются из main.py, выживает только `health`):
```python
from app.routers import health
from app.routers import workspace  # NEW

# ...

app.include_router(health.router)
app.include_router(workspace.router)
```

**Lifespan-блок** (`app/main.py:26-46`):
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    # Startup
    logger.info("Starting Telegram Followup API...")
    await init_db()
    logger.info("Database initialized")
    await recover_stuck_jobs()
    queue_worker.start()
    logger.info("Queue worker started")
    warmup_worker.start()
    logger.info("Warmup worker started")

    yield

    # Shutdown
    logger.info("Shutting down...")
    await queue_worker.stop()
    await warmup_worker.stop()
    await engine.dispose()
    logger.info("Shutdown complete")
```
**Решение для Phase 1:** D-15 запрещает трогать `app/services/`. `queue_worker` и `warmup_worker` живут в `app/services/`. Но `app/main.py` импортирует их и стартует/останавливает в lifespan. Phase 1 — **оставить как есть** (продукт частично нерабочий после Phase 1 — D-14, specifics). Воркеры стартуют, но без бизнес-данных в БД ничего не делают (пустая очередь). Альтернатива — закомментировать `queue_worker.start()` и `warmup_worker.start()` на Phase 1, что более чисто. Planner решит.

**CORS блок** (`app/main.py:56-63`) — **anti-pattern** `allow_origins=["*"]`:
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```
Заменить на ограниченный список из settings (см. CONCERNS.md + Anti-Patterns Avoid в RESEARCH §Architecture Patterns):
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,  # из env CORS_ALLOWED_ORIGINS, comma-separated
    # ...
)
```

---

### 6. `app/config.py` (MOD) — добавить supabase_jwt_secret, supabase_url; удалить api_key

**Аналог:** сам файл (полный текст — 34 строки, см. выше).

**Текущая структура** (`app/config.py:1-34`):
```python
from pydantic import ConfigDict
from pydantic_settings import BaseSettings
from functools import lru_cache
from typing import Optional


class Settings(BaseSettings):
    model_config = ConfigDict(env_file=".env", case_sensitive=False)

    # Database
    database_url: str

    # Telegram
    telegram_api_id: int
    telegram_api_hash: str

    # Security
    api_key: str            # ← УДАЛИТЬ
    encryption_key: str

    # App settings
    log_level: str = "INFO"
    max_pool_size: int = 10
    # ...


@lru_cache()
def get_settings() -> Settings:
    return Settings()
```

**После Phase 1:** добавить в `# Security` секцию:
```python
    # Supabase (NEW Phase 1)
    supabase_jwt_secret: str
    supabase_url: str

    # CORS (NEW Phase 1)
    cors_allowed_origins: str = "http://localhost:5173"   # comma-separated; парсится в свойство
```
И удалить `api_key: str`.

Конвенции для повторения:
- `snake_case` для имён полей (pydantic-settings берёт `UPPER_CASE` env vars автоматически благодаря `case_sensitive=False`).
- `Optional[str] = None` или `str = "default"` для опциональных полей (паттерн `decodo_*`).
- `@lru_cache()` на фабрике `get_settings()` — синглтон-паттерн (см. CONVENTIONS.md §Module Design).

---

### 7. `app/database.py` (MOD optional, C-04) — возможно убрать `create_all`

**Аналог:** сам файл (полный текст — 38 строк, см. выше).

**Текущий init_db** (`app/database.py:36-38`):
```python
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
```

**Pattern для не-трогания** (RESEARCH §Don't Hand-Roll + §"Open Questions §1"): **оставить как есть в Phase 1**. Сценарий A в RESEARCH §Tenant-Scoped Tables Inventory: `init_db()` создаёт все ORM-таблицы → миграция 012 ALTER-ит → OK на пустой БД.

**Если planner всё же решит фиксить (C-04 → "yes"):** заменить тело на migration runner:
```python
import os
import logging

logger = logging.getLogger(__name__)

async def init_db():
    """Apply raw SQL migrations from migrations/ in numeric order."""
    migrations_dir = os.path.join(os.path.dirname(__file__), "..", "migrations")
    files = sorted(f for f in os.listdir(migrations_dir) if f.endswith(".sql"))
    async with engine.begin() as conn:
        for fname in files:
            path = os.path.join(migrations_dir, fname)
            with open(path) as f:
                sql = f.read()
            logger.info(f"Applying migration {fname}")
            await conn.exec_driver_sql(sql)
```
**Рекомендация (из RESEARCH):** в Phase 1 оставить как есть, добавить TODO. Migration runner — отдельный Plan на Phase 5+.

---

### 8. `docker-compose.yml` (MOD) — container_name rename + env vars

**Аналог:** сам файл (полный текст — 63 строки, см. выше).

**Pattern A — container_name переименование** (D-18, см. также RESEARCH §Runtime State Inventory):
```yaml
# БЫЛО:
db:    container_name: telegram-api-db
api:   container_name: telegram-api
listener: container_name: telegram-listener

# СТАЛО:
db:    container_name: outreach-platform-db
api:   container_name: outreach-platform-api
listener: container_name: outreach-platform-listener
```
**Critical:** без этого деплой убъёт прод (см. RESEARCH §Runtime State Inventory).

**Pattern B — env var в services.api.environment** (из `docker-compose.yml:29-39`):
```yaml
environment:
  DATABASE_URL: postgresql+asyncpg://telegram_user:telegram_secure_pass_2025@db:5432/telegram_followup
  TELEGRAM_API_ID: ${TELEGRAM_API_ID}
  TELEGRAM_API_HASH: ${TELEGRAM_API_HASH}
  ENCRYPTION_KEY: ${ENCRYPTION_KEY}
  OPENAI_API_KEY: ${OPENAI_API_KEY}
  API_KEY: ${API_KEY}                  # ← УДАЛИТЬ из api-секции (но НЕ из listener — RESEARCH §Runtime)
  DECODO_HOST: ${DECODO_HOST}
  # ...
```
**После Phase 1 в api-секции:**
```yaml
environment:
  DATABASE_URL: postgresql+asyncpg://outreach_user:outreach_secure_pass_2026@db:5432/outreach_platform
  TELEGRAM_API_ID: ${TELEGRAM_API_ID}
  TELEGRAM_API_HASH: ${TELEGRAM_API_HASH}
  ENCRYPTION_KEY: ${ENCRYPTION_KEY}
  OPENAI_API_KEY: ${OPENAI_API_KEY}
  SUPABASE_JWT_SECRET: ${SUPABASE_JWT_SECRET}    # NEW
  SUPABASE_URL: ${SUPABASE_URL}                   # NEW
  CORS_ALLOWED_ORIGINS: ${CORS_ALLOWED_ORIGINS}   # NEW
  DECODO_HOST: ${DECODO_HOST}
  # ...
```
DB credentials (`telegram_user`, `telegram_secure_pass_2025`, `telegram_followup`) — planner может переименовать ради консистентности с outreach-platform, но это discretion: оставить как `telegram_user@.../telegram_followup` тоже валидно (БД новая отдельная всё равно), либо заменить на `outreach_user@.../outreach_platform`. **Рекомендация:** переименовать — раз уж переименовываем контейнеры, db credentials тоже стоит для чистоты. Это и удобнее для troubleshooting (`docker exec outreach-platform-db psql -U outreach_user`).

**Pattern C — listener секция остаётся почти как есть** (D-15 запрещает трогать services + `API_KEY` всё ещё нужен listener'у):
```yaml
listener:
  build:
    context: .
    dockerfile: Dockerfile.listener
  container_name: outreach-platform-listener     # ← только это меняем
  restart: unless-stopped
  environment:
    DATABASE_URL: ...                            # обновить если db rename
    TELEGRAM_API_ID: ${TELEGRAM_API_ID}
    TELEGRAM_API_HASH: ${TELEGRAM_API_HASH}
    ENCRYPTION_KEY: ${ENCRYPTION_KEY}
    OPENAI_API_KEY: ${OPENAI_API_KEY}
    API_KEY: ${API_KEY}                          # ОСТАВИТЬ (listener читает app.config.api_key)
```
**Landmine:** если planner удалит `api_key: str` из `app/config.py`, listener-контейнер тоже сломается (он использует тот же `Settings` класс). Решение: либо листенер не пересобираем (D-15), но тогда `app/config.py` в нём останется с `api_key` — это противоречие. **Чистое решение:** удалить `api_key` из config; в listener-коде Phase 1 не используется (раз services не трогаем) → импорт `settings.api_key` где-то может упасть на старте. Planner должен **grep по `settings.api_key` в `app/`** и убедиться что Phase 1-неактивные пути его не дёрнут. Если используется — пометить как `Optional[str] = None` пока не выпилится в Phase 2-4.

---

### 9. `requirements.txt` (MOD) — добавить bcrypt + pytest стек

**Аналог:** сам файл (29 строк, см. выше).

**Текущий формат** (из `requirements.txt:5-22`):
```
# FastAPI
fastapi==0.109.0
uvicorn[standard]==0.27.0
python-multipart==0.0.6

# Database
sqlalchemy==2.0.25
asyncpg==0.29.0
alembic==1.13.1                  # ← legacy, не использовать; но не удалять в Phase 1

# Security
cryptography==42.0.0
python-jose[cryptography]==3.3.0
```
Конвенция: секции `# Section name` + точная закреплённая версия (`==`), без диапазонов (кроме pydantic — там `>=2.8,<3.0`).

**Phase 1 добавления:**
```
# Security
cryptography==42.0.0
python-jose[cryptography]==3.3.0
bcrypt>=4.1.0,<5.0                   # NEW: для workspace API-key хеширования

# Testing (NEW Phase 1 — Wave 0)
pytest>=8.0
pytest-asyncio>=0.23
```
**httpx уже есть** (`httpx==0.26.0` строка 26) — для AsyncClient тестов FastAPI ничего больше не нужно (RESEARCH §Validation Architecture).

**Альтернатива (если planner предпочитает разделить prod и dev):** создать `requirements-dev.txt` с pytest+pytest-asyncio. RESEARCH §Validation Architecture допускает оба варианта. В существующем репо `requirements-dev.txt` нет.

---

### 10–13. Тесты — `tests/conftest.py`, `tests/test_migration_012.py`, `tests/test_auth_dep.py`, `tests/test_workspace_router.py`

**Аналог:** **нет** — в репо нет ни одного теста (подтверждено `ls tests/` отсутствует, в requirements.txt нет pytest). См. RESEARCH §Validation Architecture для полного дизайна Wave 0.

**Конвенции, которые надо унаследовать (хотя файлов нет, есть кодовые конвенции из CONVENTIONS.md):**
- snake_case имена файлов: `test_migration_012.py`, `test_auth_dep.py`, `test_workspace_router.py`.
- Async-функции тестов: `async def test_*` + `pytest_asyncio` (через `asyncio_mode = "auto"` в `pyproject.toml`).
- Логирование через `logging.getLogger(__name__)`.

**Pattern A — conftest fixture для async DB session** (отсутствует в репо; шаблон из RESEARCH §Code Examples 2 + sqlalchemy docs):
```python
# tests/conftest.py
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.database import AsyncSessionLocal


@pytest_asyncio.fixture
async def async_db_session() -> AsyncSession:
    """Изолированная DB-сессия с rollback после теста."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.rollback()
            await session.close()


@pytest_asyncio.fixture
async def async_client() -> AsyncClient:
    """httpx.AsyncClient с in-process ASGITransport."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
def valid_supabase_jwt():
    """Фабрика: создаёт валидный HS256 JWT с заданным sub/email."""
    from jose import jwt
    from app.config import get_settings
    settings = get_settings()

    def _factory(sub: str = "test-user-uuid", email: str | None = "test@example.com"):
        claims = {"sub": sub, "email": email, "aud": "authenticated",
                  "exp": 9999999999}
        return jwt.encode(claims, settings.supabase_jwt_secret, algorithm="HS256")
    return _factory
```

**Pattern B — DB schema introspection test** (нет аналога; шаблон):
```python
# tests/test_migration_012.py
import pytest
from sqlalchemy import text

TENANT_SCOPED_TABLES = [
    "senders", "messages_log", "contacts_cache", "ai_contexts",
    "message_queue", "conversations", "warmup_pool", "warmup_sessions",
    "warmup_messages", "proxy_pool", "context_contact_assignments",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("table", TENANT_SCOPED_TABLES)
async def test_migration_012_adds_workspace_id(async_db_session, table):
    """Каждая tenant-scoped таблица должна иметь NOT NULL workspace_id FK."""
    result = await async_db_session.execute(text(f"""
        SELECT column_name, is_nullable, data_type
        FROM information_schema.columns
        WHERE table_name = :t AND column_name = 'workspace_id'
    """), {"t": table})
    row = result.fetchone()
    assert row is not None, f"{table}: workspace_id column missing"
    assert row[1] == "NO", f"{table}: workspace_id is nullable"
    assert row[2] == "uuid"
```

**Pattern C — endpoint integration test** (нет аналога; шаблон):
```python
# tests/test_auth_dep.py
import pytest


@pytest.mark.asyncio
async def test_no_auth_rejected(async_client):
    response = await async_client.get("/api/v1/workspace")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_valid_jwt_creates_workspace(async_client, valid_supabase_jwt):
    token = valid_supabase_jwt(sub="new-user-uuid", email="new@example.com")
    response = await async_client.post(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["workspace_id"]
    assert body["source"] == "jwt"
```

---

## Shared Patterns

### Shared 1: `Depends(get_db)` для DB session injection

**Source:** `app/database.py:28-33`
```python
async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
```
**Apply to:** `app/utils/auth.py` (auth_dep), `app/routers/workspace.py` (все endpoints). Параметр всегда называется `db: AsyncSession`, тип-аннотация обязательна.

---

### Shared 2: HTTPException с структурированным detail

**Source:** CONVENTIONS.md §Error Handling + текущий `app/routers/auth.py:13-22`
```python
# Текущий стиль (plain string detail) — допустимо для простых случаев:
raise HTTPException(status_code=401, detail="API key is missing. Provide X-API-Key header.")

# Структурированный стиль (для нового кода — рекомендован):
raise HTTPException(
    status_code=401,
    detail={"code": "AUTH_REQUIRED", "message": "Provide Authorization Bearer or X-Workspace-Key"}
)
```
**Apply to:** все новые `HTTPException` в `app/utils/auth.py` и `app/routers/workspace.py`. Код-имена в `SCREAMING_SNAKE_CASE`: `AUTH_REQUIRED`, `TOKEN_EXPIRED`, `TOKEN_INVALID`, `API_KEY_INVALID`, `WORKSPACE_NOT_FOUND`.

---

### Shared 3: Логирование

**Source:** CONVENTIONS.md §Logging + `app/routers/senders.py:12, 48, 50` + `app/main.py:17-21`
```python
# Модуль:
logger = logging.getLogger(__name__)

# Использование:
logger.info(f"[{slug}] ...")              # state transitions
logger.warning(f"⚠️ ...")                 # recoverable issues
logger.error(f"...", exc_info=True)       # exceptions, всегда с exc_info=True
```
**Apply to:** `app/utils/auth.py` (`auth_dep` логирует source, prefix для api-key — НЕ полный токен, НЕ полный JWT). **Anti-pattern из CONCERNS.md:** не логировать API-key целиком, только первые 12 символов (`prefix`).

---

### Shared 4: Async-only DB операции

**Source:** CLAUDE.md "Архитектурные правила" + `app/database.py:28-33` + `app/routers/senders.py:54-64`
- `async def` для каждой функции, которая трогает DB или I/O.
- `await db.execute(...)`, `await db.commit()`, `await db.refresh(obj)`.
- **bcrypt и любой CPU-bound sync код** — оборачивать в `await asyncio.to_thread(...)` (см. Pitfall 3).

**Apply to:** все три новых файла кода (`auth.py`, `workspace.py`) + любые тесты, которые читают БД.

---

### Shared 5: workspace_id фильтрация (TODO RLS-метки)

**Source:** D-04, RESEARCH §Anti-Patterns to Avoid
Каждый SQL-запрос в новом коде, который читает tenant-scoped данные:
```python
# Pattern:
result = await db.execute(
    select(WorkspaceApiKey).where(
        WorkspaceApiKey.workspace_id == ctx.workspace_id,   # tenant filter
        WorkspaceApiKey.id == key_id,
    )
)
# TODO(v2-rls): replaced by RLS policy app.workspace_id
```
**Apply to:** `app/routers/workspace.py` — все SELECT/UPDATE/DELETE для `workspace_api_keys`. В Phase 1 только этот один роутер, но соглашение фиксируется для Phase 2-4.

---

### Shared 6: UUID PK с Python default

**Source:** `app/models/__init__.py:32` + повсюду
```python
id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
```
**Apply to:** все три новые модели (`Workspace`, `UserWorkspace`, `WorkspaceApiKey`). Python-default, не `server_default=text('gen_random_uuid()')` — оставляет ORM-уровню контроль; на SQL-уровне в миграции 012 — `DEFAULT gen_random_uuid()` (для случаев когда INSERT без явного id, например через psql).

---

### Shared 7: Section dividers в моделях

**Source:** `app/models/__init__.py:177, 233`
```python
# ─── Warmup ───────────────────────────────────────────────────────────────────
# ─── Rotation ─────────────────────────────────────────────────────────────────
```
**Apply to:** при добавлении 3 новых моделей в `__init__.py` — поставить divider:
```python
# ─── Multi-tenant foundation ─────────────────────────────────────────────────
```
В начале файла, до `class Sender`, либо в самом начале как самые «корневые» сущности.

---

## No Analog Found

| Файл | Роль | Data Flow | Причина |
|---|---|---|---|
| `tests/conftest.py` | test fixtures | infrastructure | В репо тестов нет вообще. Используем RESEARCH §Code Examples + §Validation Architecture как образец. |
| `tests/test_migration_012.py` | DB-introspection unit | DDL validation | Аналогов нет — DB introspection через `information_schema.columns`. |
| `tests/test_auth_dep.py` | integration | request-response | Аналогов нет — `httpx.AsyncClient` + `ASGITransport` (httpx 0.26.0 поддерживает). |
| `tests/test_workspace_router.py` | integration | request-response | Аналогов нет — тот же шаблон. |

**Совет планеру:** при отсутствии аналогов следовать паттернам из RESEARCH §Validation Architecture (Wave 0) + CONVENTIONS.md (snake_case имена, async-функции, `logger = logging.getLogger(__name__)`).

---

## Quick Reference Table — какой файл → какой паттерн

| Новый/изменённый файл | Главный аналог | Конкретные строки для копирования |
|---|---|---|
| `migrations/012_workspace.sql` | `005_warmup.sql` (CREATE TABLE), `010_missing_indexes.sql` (CREATE INDEX), `011_sender_auth_status.sql` (ALTER TABLE ADD COLUMN) | `005:3-12`, `010:9-11`, `011:5-7` |
| `app/models/__init__.py` (новые модели) | `Sender` `app/models/__init__.py:29-48`; `WarmupPool` `app/models/__init__.py:179-189`; `AIContext` `app/models/__init__.py:87-106` | копировать структуру Column-defs |
| `app/models/__init__.py` (workspace_id на 11 моделей) | сам файл (паттерн `Column(UUID, ForeignKey('table.id', ondelete='CASCADE'), nullable=False)`) | одна строка после `id =` |
| `app/utils/auth.py` | `app/routers/auth.py:1-25` (структура), `app/routers/senders.py:1-11` (импорты), CONVENTIONS.md §Error Handling (HTTPException формат) | заголовок 1-11; HTTPException 13-22 (с обновлённым detail) |
| `app/routers/workspace.py` | `app/routers/contexts.py:1-260` (CRUD-структура), `app/routers/senders.py:67-105` (POST + refresh) | контексты — полностью как шаблон |
| `app/main.py` | сам файл `app/main.py:11-14, 66-75` (что удалить); `app/routers/health.py` (что остаётся) | удалить 9 включений, добавить workspace |
| `app/config.py` | сам файл `app/config.py:1-34` (добавить 3 поля, удалить api_key) | добавить в секцию `# Security` |
| `app/database.py` | сам файл (C-04 — оставить как есть) | без изменений (рекомендация) |
| `docker-compose.yml` | сам файл `docker-compose.yml:1-63` (rename containers, env vars) | 3 `container_name` строки + env в `api` секции |
| `requirements.txt` | сам файл (формат `pkg==version` или `pkg>=X,<Y`) | добавить bcrypt, pytest, pytest-asyncio |
| `tests/*` | **нет аналога** — следовать RESEARCH §Code Examples + §Validation Architecture | шаблоны выше |

---

## Metadata

**Analog search scope:**
- `/Users/andrewbruce/Documents/outreach-platform/app/` (всё содержимое)
- `/Users/andrewbruce/Documents/outreach-platform/migrations/` (миграции 001-011)
- `/Users/andrewbruce/Documents/outreach-platform/docker-compose.yml`
- `/Users/andrewbruce/Documents/outreach-platform/requirements.txt`
- `/Users/andrewbruce/Documents/outreach-platform/.planning/codebase/` (STRUCTURE.md, CONVENTIONS.md)

**Files scanned:** 14 (8 router files, 1 models file, 1 database, 1 config, 1 main, 4 migrations + 1 anti-pattern migration 003)

**Pattern extraction date:** 2026-05-21

**Notes для планера:**
1. **Главный CRUD-шаблон** = `app/routers/contexts.py` (260 строк, ровно та форма что нужна для workspace.py). При сомнении что копировать — смотреть туда.
2. **Главный SQL-шаблон** = композиция `005_warmup.sql` (CREATE TABLE с UUID/FK/CHECK) + `010_missing_indexes.sql` (partial-индексы) + `011_sender_auth_status.sql` (ALTER ADD COLUMN).
3. **Главный auth-anti-pattern** = `app/routers/auth.py` (full replace), `app/routers/senders.py:36-50` (`_restart_listener` через subprocess — не копировать).
4. **Тестов нет** — Wave 0 строит инфраструктуру с нуля, аналогов в репо для подражания нет; шаблоны строго из RESEARCH §Validation Architecture.
