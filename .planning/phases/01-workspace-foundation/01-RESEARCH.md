# Phase 1: Workspace Foundation — Research

**Researched:** 2026-05-21
**Domain:** Multi-tenant Postgres schema migration + Supabase JWT validation + dual-auth FastAPI dependency
**Confidence:** HIGH (стек и паттерны проверены по коду; внешние API верифицированы через официальные docs)

---

## Summary

Phase 1 разрезает single-tenant inheritance от telegram-api на multi-tenant фундамент. Три независимых, но взаимосвязанных рабочих потока: (1) raw-SQL миграция 012, которая добавляет `workspace_id` на 11 существующих таблиц + создаёт 3 новые (`workspaces`, `user_workspaces`, `workspace_api_keys`); (2) новый файл `app/utils/auth.py` с `AuthCtx` Pydantic-моделью и единым `auth_dep` FastAPI Depends, ветвящимся по заголовку (Bearer JWT vs `X-Workspace-Key`); (3) новый router `app/routers/workspace.py` (5–6 endpoint-ов) + удаление 10 `include_router` из `app/main.py` + удаление файла `app/routers/auth.py` (старый `verify_api_key`).

Главный landmark — Supabase в 2025-2026 мигрирует с HS256 на ES256/JWKS, новые проекты с октября 2025 default-но получают asymmetric keys. Решение CONTEXT.md D-05 (HS256 + local validation) **остаётся валидным**, но только если пользователь явно использует legacy JWT Secret из dashboard, либо создал проект до октября 2025. Это надо явно отметить как landmine для planner'а (см. Pitfall 1).

Второй важный момент — `python-jose` (упомянутый в CONTEXT.md как уже установленный) **deprecated** с 2023-2024, FastAPI официально мигрировал на PyJWT. Тут две опции: либо оставить python-jose (он установлен, работает, риск низкий для HS256), либо параллельно поставить PyJWT — это discretion-решение planner'а.

**Primary recommendation:** Использовать `python-jose` (уже в requirements.txt, риск замены неоправдан для Phase 1), декодировать JWT с `audience='authenticated'`, `algorithms=['HS256']`, секрет из Supabase Dashboard → Settings → API → "JWT Secret" (legacy). Миграция 012 — одна транзакция, идемпотентная, raw SQL, без enum-типов (использовать `VARCHAR` + `CHECK` constraint для `user_workspaces.role`, повторяя паттерн `senders.auth_status`). API key prefix — 12 символов (`wsk_` + первые 8 url-safe из 32-byte random), BTREE индекс на `(prefix) WHERE revoked_at IS NULL` для быстрого lookup.

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**БД и стратегия миграции:**
- **D-01:** Стартовое состояние БД outreach-platform — пустая, новый Postgres-контейнер в собственном `docker-compose.yml`. Данные из prod telegram-api не переносятся. AGS Foods заводится заново как обычный workspace при необходимости.
- **D-02:** Миграция `012_workspace.sql` добавляет `workspace_id UUID NOT NULL` FK на всех арендатор-скоупленных таблицах в одной транзакции. Без nullable-фазы, без backfill — БД пустая.
- **D-03:** Все ресурсные таблицы scoped по workspace, **включая `proxy_pool` и `warmup_pool`**. Модель BYO-proxy: клиент сам приносит свои прокси. Warmup только между аккаунтами одного workspace.
- **D-04:** Изоляция enforced на уровне приложения: helper `get_db_scoped(workspace_id)` или явный `.where(Model.workspace_id == ctx.workspace_id)` в каждом репо-запросе. DB-уровень: `NOT NULL` FK + композитные индексы. **Postgres RLS отложен на v2**. Оставить TODO-метки в коде.

**Supabase JWT валидация и источник workspace_id:**
- **D-05:** Валидация JWT — локально, HS256 через `python-jose` (уже в requirements.txt). `SUPABASE_JWT_SECRET` из project settings → API в Supabase, добавляется в `app/config.py`. Никаких HTTP-вызовов в Supabase на запрос, никакого JWKS-кэша.
- **D-06:** `workspace_id` НЕ хранится в JWT claims. Источник — новая локальная таблица `user_workspaces (supabase_user_id text, workspace_id UUID, role enum, created_at)` с индексом по `supabase_user_id`. AuthDep декодит JWT → берёт `sub` → `SELECT workspace_id, role FROM user_workspaces WHERE supabase_user_id = $1`.
- **D-07:** Никакого in-memory кэша `user_id → workspace_id` в v1.

**Auto-create workspace при первом входе:**
- **D-08:** Lazy-создание в FastAPI AuthDep: валидный JWT + пустой lookup в `user_workspaces` → создать `workspace` + `user_workspaces` в одной DB-транзакции, вернуть AuthCtx с новым workspace_id.
- **D-09:** Имя нового workspace по умолчанию = email пользователя из JWT claim (или `'My Workspace'` если email отсутствует).
- **D-10:** Схема `user_workspaces` сразу many-to-many с `role` enum (`owner`/`admin`/`member`), **без UNIQUE** на `supabase_user_id`. В v1 бизнес-инвариант: 1 user = 1 workspace.

**Dual auth: JWT (UI) + Workspace API-ключ (n8n):**
- **D-11:** Один FastAPI Depends — `AuthDep` в `app/utils/auth.py`. Ветвится по заголовку:
  - `Authorization: Bearer <token>` → декодим как Supabase JWT → lookup workspace_id
  - `X-Workspace-Key: wsk_<random>` → парсим prefix → bcrypt-проверка → workspace_id из строки ключа
  - Ни тот, ни другой → 401
- **D-12:** Возвращаемый объект — `AuthCtx(workspace_id: UUID, user_id: str | None, source: Literal['jwt','api_key'], role: str | None)`. Все новые роутеры принимают `ctx: AuthCtx = Depends(auth_dep)`.
- **D-13:** Workspace API-ключ:
  - Формат токена `wsk_` + 32 url-safe random bytes (через `secrets.token_urlsafe`).
  - В БД хранится: `workspace_api_keys(id, workspace_id FK, prefix VARCHAR(12), bcrypt_hash TEXT, name VARCHAR(50), created_at, last_used_at, revoked_at)`. Plaintext-токен пользователь видит ровно один раз в ответе POST-создания.
  - Lookup: парсим prefix → `SELECT * WHERE prefix=$1 AND revoked_at IS NULL` → bcrypt.verify над кандидатами.
  - Регенерация = revoke + create new. У одного workspace может быть несколько активных ключей одновременно.
- **D-14:** Старый `verify_api_key` (X-API-Key) **полностью удаляется**. Все 11 старых роутеров **выпиливаются из `app/main.py`**. После Phase 1 продукт не отвечает на бизнес-запросы — только на новые workspace/auth-эндпоинты.
- **D-15:** `app/services/` **не трогается** в Phase 1.

### Claude's Discretion

- **C-01:** Точное имя нового AuthCtx/AuthDep файла (`app/utils/auth.py` рекомендовано) — planner может выбрать другое.
- **C-02:** Точный формат `workspace_api_keys.prefix` — 8 или 12 символов.
- **C-03:** Список endpoint-ов workspace-скелета (минимум: `POST /api/v1/auth/me`, `GET /api/v1/workspace`, `PATCH /api/v1/workspace`, `POST /api/v1/workspace/api-keys`, `GET /api/v1/workspace/api-keys`, `DELETE /api/v1/workspace/api-keys/{id}`).
- **C-04:** Решение по `init_db()` `Base.metadata.create_all` в `app/database.py` — оставить или заменить.

### Deferred Ideas (OUT OF SCOPE)

**Для Phase 2:**
- UI для загрузки клиентом своих прокси
- Перепись `senders.py` + `onboarding.py` поверх workspace_id
- Решение про `subprocess.run(["docker", "restart"])` в senders.py

**Для Phase 3/4:**
- Перезапись `send.py`, `conversations.py`, `contexts.py`, `queue.py` router, `check_contacts.py`, `warmup.py` router, `proxy_pool.py` поверх workspace_id

**Для v2:**
- Postgres RLS на всех арендатор-скоупленных таблицах
- Выбор active workspace для пользователя с несколькими workspace
- Custom JWT claim `workspace_id` через Supabase Edge Function
- In-memory кэш user_id → workspace_id
- Team support (TEAM-01, TEAM-02)

**Tech debt:**
- `init_db()` `Base.metadata.create_all` — решение оставлено за planner'ом (C-04).

</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| TENT-01 | Все сущности (senders, contexts, contacts, queue, conversations) изолированы по workspace_id | Миграция 012 добавляет `workspace_id UUID NOT NULL` FK на 9 таблиц (см. "Tenant-Scoped Tables Inventory"). Сам filter — Phase 2-4 при рерайте роутеров. В Phase 1 уровень БД готов (CHECK NOT NULL заставит будущие INSERT-ы валиться без workspace_id). |
| TENT-02 | Workspace создаётся автоматически при регистрации | D-08 lazy auto-create в `auth_dep` — при первом JWT-запросе без записи в `user_workspaces` создаётся `workspace` + `user_workspaces` в одной транзакции. Endpoint-триггер: `POST /api/v1/auth/me` (см. "Endpoint Skeleton"). |
| TENT-03 | Workspace имеет уникальный API-ключ для интеграций (n8n и др.) | Таблица `workspace_api_keys` + endpoints `POST/GET/DELETE /api/v1/workspace/api-keys`. Формат `wsk_` + token_urlsafe(32). Bcrypt-hash в БД. |
| TENT-04 | Запросы к API без валидного workspace-контекста отклоняются (403) | `auth_dep` бросает 401 (нет учётки) или 403 (учётка валидна, но workspace не найден / revoked). Все новые роутеры `Depends(auth_dep)` → `ctx.workspace_id` гарантированно UUID. |
| AUTH-01 | Email → magic link на почту | Phase 1 backend этого не делает: магия magic-link полностью на стороне Supabase (frontend Lovable + Supabase Auth). Phase 1 принимает уже выданный JWT. |
| AUTH-02 | Magic link → JWT-сессия (Supabase) | Аналогично AUTH-01: Supabase выдаёт JWT, FastAPI его валидирует. Frontend кладёт в `Authorization: Bearer <token>`. |
| AUTH-03 | FastAPI верифицирует Supabase JWT и извлекает workspace_id | `app/utils/auth.py` — функция `_decode_supabase_jwt(token)` (HS256, `audience='authenticated'`), затем lookup `user_workspaces.supabase_user_id == sub`. См. "Code Examples". |
| AUTH-04 | Сессия сохраняется через browser refresh | Supabase SDK на frontend сам рефрешит token. Phase 1 backend stateless — каждый запрос приходит с JWT, ничего не сохраняем. |

</phase_requirements>

## Project Constraints (from CLAUDE.md)

| Constraint | Source line | Phase 1 implication |
|------------|-------------|---------------------|
| **Не пиши код сразу** — объясни 2-3 предложениями, дождись подтверждения | "Главное правило" | Planner создаёт PLAN.md, не код. Каждая task должна позволять executor'у объяснить намерение перед изменением. |
| **Общение по-русски, код/коммиты по-английски** | "Главное правило" | RESEARCH.md, PLAN.md — русский. Имена файлов, функций, SQL-полей, commit messages — английский. |
| **Только raw SQL миграции в `migrations/`** | "Архитектурные правила" | Миграция 012 — `.sql` файл, нумерация `012_workspace.sql`. **Никакой Alembic.** Хоть он и в requirements.txt — это legacy. |
| **Идемпотентность через `IF NOT EXISTS`/`IF EXISTS`** | "Архитектурные правила" | Все `CREATE TABLE` и `ALTER TABLE ... ADD COLUMN` в 012 — с `IF NOT EXISTS`. `CREATE INDEX` — тоже. |
| **Async everywhere** — `AsyncSession`, никаких sync операций | "Архитектурные правила" | `auth_dep` — `async def`. JWT-декод сам по себе sync (CPU-bound, ~µs), но DB lookup — `await`. bcrypt.checkpw — sync (~80ms на cost=12) — нужно обернуть в `asyncio.to_thread`, иначе блокируется event loop. **Это landmine.** |
| **Никаких `time.sleep()`, `print()`, sync `requests`** | "Архитектурные правила" | Логирование через `logging.getLogger(__name__)`. Любые HTTP-вызовы — `httpx` async (но в Phase 1 их не должно быть — D-05 запрещает HTTP в Supabase). |
| **API_KEY не в логах** | "Архитектурные правила" | `wsk_` ключи никогда не логировать целиком — только prefix (12 символов). JWT-токены тоже не логировать. |
| **Очередь / FloodWait retry — не трогать** | "Архитектурные правила" | Phase 1 не касается `app/services/queue.py` (D-15). Никакого риска. |

---

## Standard Stack

### Core (всё уже в `requirements.txt`)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| FastAPI | 0.109.0 | Router-уровень, Depends-инъекция | Уже в проекте, не меняем |
| SQLAlchemy | 2.0.25 + asyncpg 0.29 | ORM + async PostgreSQL | Установлен, паттерн `AsyncSession` уже везде |
| pydantic | >=2.8,<3.0 | `AuthCtx`, request/response модели | v2 идиомы — `ConfigDict`, `model_config`, `Literal` |
| pydantic-settings | >=2.3,<3.0 | `Settings` с env vars | `app/config.py` уже использует |
| python-jose[cryptography] | 3.3.0 | JWT decode HS256 | **Уже установлен.** Deprecated, но для HS256 работает (см. Pitfall 2) |
| cryptography | 42.0.0 | Транзитивно нужна для python-jose | Уже установлен |

### Supporting (нужно добавить)

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| **bcrypt** | **>=4.1.0,<5.0** | Хеширование `wsk_` API-ключей + verify | **Нет в `requirements.txt`** (там нет ни bcrypt, ни passlib). Нужно добавить отдельным пакетом, не через passlib (passlib deprecated, FastAPI отказался). |

### Alternatives Considered

| Instead of | Could Use | Tradeoff | Recommendation |
|------------|-----------|----------|----------------|
| python-jose 3.3.0 | PyJWT >=2.8 | PyJWT активно поддерживается, FastAPI официально рекомендует. API почти идентичный. | **Оставить python-jose** для Phase 1 — он уже в requirements.txt, риск не оправдан. Заменить в v2 при техдолге. |
| bcrypt direct | passlib + bcrypt | passlib не поддерживается с 2020, ломается на Python 3.13 | **bcrypt напрямую** — `bcrypt.hashpw`, `bcrypt.checkpw`. Минус 1 транзитивная зависимость. |
| bcrypt | argon2-cffi | Argon2 современнее, но bcrypt достаточен для API-ключей (это не пользовательские пароли, длина >32 символов) | bcrypt — низкий риск, общепринят. |
| Local HS256 validation | JWKS endpoint + ES256 | ES256 — будущее Supabase (новые проекты с окт. 2025 → ES256 default). Но требует кэш JWKS, http-вызов. | **Оставить HS256** для Phase 1 (D-05 locked). Но **зафиксировать в коде TODO** про миграцию (см. Pitfall 1). |

**Installation:**
```bash
# Добавить в requirements.txt:
bcrypt>=4.1.0,<5.0
```

**Version verification (на 2026-05-21):**
- `python-jose==3.3.0` — последний релиз 2022-06-23 (3+ года без обновлений). Работает с HS256 без проблем.
- `bcrypt==4.1.3` (последний на pypi) — активно поддерживается.
- `PyJWT==2.8.0` (последний) — активная разработка, если planner решит мигрировать.

*Версии не запускались через `pip index versions` локально — взяты из публичных источников. Planner может верифицировать в Wave 0 если нужно.*

---

## Architecture Patterns

### Recommended Project Structure

```
app/
├── utils/
│   ├── __init__.py
│   └── auth.py              # НОВЫЙ: AuthCtx, auth_dep, _decode_jwt, _verify_api_key,
│                            #        _create_workspace_if_missing helpers
├── routers/
│   ├── workspace.py         # НОВЫЙ: GET/PATCH /api/v1/workspace,
│   │                        #        POST/GET/DELETE /api/v1/workspace/api-keys
│   ├── auth_me.py           # НОВЫЙ (или внутри workspace.py): POST /api/v1/auth/me
│   ├── health.py            # ОСТАВИТЬ: единственный старый router, который выживает
│   ├── auth.py              # УДАЛИТЬ (старый verify_api_key)
│   └── (остальные)          # ОСТАЮТСЯ как файлы, но НЕ подключаются в main.py
├── models/__init__.py       # ДОБАВИТЬ: Workspace, UserWorkspace, WorkspaceApiKey;
│                            #        workspace_id Column на 9 существующих моделей
├── schemas/__init__.py      # ДОБАВИТЬ: WorkspaceResponse, ApiKeyCreateResponse и т.д.
├── config.py                # ДОБАВИТЬ: supabase_jwt_secret, supabase_url;
│                            #        УДАЛИТЬ: api_key
├── main.py                  # УДАЛИТЬ: 10 include_router; ДОБАВИТЬ: workspace router;
│                            #        ОГРАНИЧИТЬ: CORS до Lovable домена
└── database.py              # ВОЗМОЖНО: убрать Base.metadata.create_all (C-04)

migrations/
└── 012_workspace.sql        # НОВЫЙ: 1 транзакция, ~150 строк

docker-compose.yml           # ДОБАВИТЬ: SUPABASE_JWT_SECRET, SUPABASE_URL в env;
                            #        УДАЛИТЬ: API_KEY (можно оставить для listener,
                            #                 т.к. listener не трогаем)
```

### Pattern 1: Raw SQL Migration с транзакцией

**Когда применять:** Любая миграция, добавляющая или меняющая структуру таблиц.

**Каноничный паттерн (из `005_warmup.sql`, `010_missing_indexes.sql`):**

```sql
-- migrations/012_workspace.sql
-- Multi-tenant foundation: workspaces, user_workspaces, workspace_api_keys
-- + workspace_id FK на все tenant-scoped таблицы.
-- БД должна быть пустой (D-01). Все операторы идемпотентны.

BEGIN;

-- 1. workspaces (root tenant table)
CREATE TABLE IF NOT EXISTS workspaces (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        VARCHAR(100) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2. user_workspaces (many-to-many, D-10)
CREATE TABLE IF NOT EXISTS user_workspaces (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    supabase_user_id    TEXT NOT NULL,
    workspace_id        UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    role                VARCHAR(20) NOT NULL DEFAULT 'owner',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT user_workspaces_role_check
        CHECK (role IN ('owner', 'admin', 'member'))
);

CREATE INDEX IF NOT EXISTS idx_user_workspaces_supabase_user_id
    ON user_workspaces(supabase_user_id);

-- 3. workspace_api_keys
CREATE TABLE IF NOT EXISTS workspace_api_keys (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    prefix        VARCHAR(12) NOT NULL,
    bcrypt_hash   TEXT NOT NULL,
    name          VARCHAR(50) NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at  TIMESTAMPTZ,
    revoked_at    TIMESTAMPTZ
);

-- partial index: только активные ключи участвуют в lookup
CREATE INDEX IF NOT EXISTS idx_workspace_api_keys_prefix_active
    ON workspace_api_keys(prefix)
    WHERE revoked_at IS NULL;

-- 4. ALTER TABLE на каждой tenant-scoped таблице
ALTER TABLE senders
    ADD COLUMN IF NOT EXISTS workspace_id UUID NOT NULL
        REFERENCES workspaces(id) ON DELETE CASCADE;

-- ... повторить для остальных 8 таблиц (см. "Tenant-Scoped Tables Inventory")

-- 5. Композитные индексы для будущих query patterns
CREATE INDEX IF NOT EXISTS idx_senders_workspace
    ON senders(workspace_id);

-- ... остальные composite indexes (см. ниже)

COMMIT;
```

**Важно:**
- `gen_random_uuid()` — стандартная Postgres 13+ функция, не требует extension в Postgres 16. **Не использовать `uuid_generate_v4()`** — это `uuid-ossp`, требует CREATE EXTENSION.
- `TIMESTAMPTZ` (не `TIMESTAMP`) — соответствует ORM `DateTime(timezone=True)`.
- `ON DELETE CASCADE` для FK на `workspaces` — удаление workspace удаляет все его данные (требование SaaS — "удалил аккаунт = удалил всё").
- **Нет `uq_user_workspaces_supabase_user_id`** — D-10 запрещает unique constraint, в v2 будет many-to-many.

### Pattern 2: FastAPI Dual-Auth Dependency

**Когда применять:** Все новые endpoint-ы, кроме `GET /api/v1/health`.

**Структура файла `app/utils/auth.py`:**

```python
"""
Dual-auth dependency for outreach-platform.

Two ingress paths:
  1. Authorization: Bearer <Supabase JWT>   — UI (Lovable frontend)
  2. X-Workspace-Key: wsk_<random>          — Integrations (n8n, ad-hoc scripts)

Both resolve to AuthCtx(workspace_id, user_id, source, role).
Lazy workspace creation: valid JWT + no user_workspaces row → create workspace
  + user_workspaces row in one transaction (D-08).
"""

from typing import Literal, Optional
from uuid import UUID
import asyncio
import logging
import secrets

import bcrypt
from fastapi import Depends, Header, HTTPException
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models import UserWorkspace, Workspace, WorkspaceApiKey

logger = logging.getLogger(__name__)
settings = get_settings()


class AuthCtx(BaseModel):
    """Resolved auth context for the current request."""
    workspace_id: UUID
    user_id: Optional[str]                    # supabase sub when source='jwt', иначе None
    source: Literal["jwt", "api_key"]
    role: Optional[str]                       # 'owner'/'admin'/'member' для JWT; None для API key


async def auth_dep(
    authorization: Optional[str] = Header(None),
    x_workspace_key: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
) -> AuthCtx:
    # ... ветвление см. в Code Examples
```

**Использование в роутерах:**

```python
@router.get("/workspace")
async def get_workspace(
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    # ctx.workspace_id гарантированно UUID — никогда не None
    workspace = await db.execute(
        select(Workspace).where(Workspace.id == ctx.workspace_id)
    )
    ...
```

**Anti-pattern (не использовать в Phase 1):**
- `dependencies=[Depends(auth_dep)]` на уровне `APIRouter(...)` — теряем `ctx` в хендлерах. Всегда инъектим как параметр.
- Раздельные depends `jwt_only_dep` и `api_key_only_dep` — нарушает D-11 "один Depends". В v2 можно добавить, если потребуется.

### Pattern 3: ORM Model с workspace_id

**Когда применять:** Все новые модели и все 9 существующих tenant-scoped моделей.

**Pattern (соответствует существующим ORM-классам):**

```python
class Workspace(Base):
    __tablename__ = "workspaces"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now())


class UserWorkspace(Base):
    __tablename__ = "user_workspaces"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    supabase_user_id = Column(Text, nullable=False, index=True)
    workspace_id = Column(UUID(as_uuid=True),
                          ForeignKey("workspaces.id", ondelete="CASCADE"),
                          nullable=False)
    role = Column(String(20), nullable=False, server_default="'owner'")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    workspace = relationship("Workspace")
```

**Замечание про enum:**
- Существующие модели используют `SQLEnum(MessageType)` (например `MessageLog.message_type`). Это генерирует **Postgres TYPE** через `Base.metadata.create_all`, но **отсутствует в raw SQL миграциях** (см. landmine ниже).
- Для `UserWorkspace.role` рекомендую **String(20) + CHECK constraint в SQL миграции** — повторяет паттерн `senders.auth_status` (миграция 011) и `senders.role` (миграция 003). **НЕ повторять anti-pattern `Sender.role` без CHECK** — добавить именно CHECK в SQL.

### Anti-Patterns to Avoid

- **CORS `allow_origins=["*"]`** (текущий `app/main.py` line 59): при рерайте main.py заменить на конкретный Lovable-домен из settings: `allow_origins=settings.cors_allowed_origins`.
- **`Sender.role` без CHECK constraint** (CONCERNS.md): для `user_workspaces.role` сразу CHECK + Python enum (или `Literal['owner','admin','member']` в Pydantic).
- **bcrypt в event loop** (синхронный — блокирует на ~80ms): `await asyncio.to_thread(bcrypt.checkpw, plaintext, hash)`.
- **`return raw_token` где-то кроме POST /workspace/api-keys**: токен показывается **только в ответе на создание**, в GET — никогда.
- **Хранение plaintext-токена**: только bcrypt-hash + prefix.
- **API-key в логах**: `logger.info(f"key={key}")` — нельзя. Логируем только `prefix` (12 символов).
- **`Base.metadata.create_all` + raw migrations** (CONCERNS.md): см. Pitfall 4 ниже.

---

## Tenant-Scoped Tables Inventory

Получено grep-ом по `app/models/__init__.py` (все `__tablename__`):

| # | Table | Module | Scope decision | Comment |
|---|-------|--------|----------------|---------|
| 1 | `senders` | `Sender` | **Tenant-scoped** | Очевидно. Один клиент = свой набор аккаунтов. |
| 2 | `messages_log` | `MessageLog` | **Tenant-scoped** | История исходящих. Можно вывести через `sender.workspace_id`, но **прямой `workspace_id` нужен** для query patterns "статистика по workspace без JOIN на senders". |
| 3 | `contacts_cache` | `ContactCache` | **Tenant-scoped** | Кэш Telegram-resolve привязан к sender → к workspace. Нужно прямое поле для D-04 enforcement. |
| 4 | `ai_contexts` | `AIContext` | **Tenant-scoped** | Один промпт на workspace (AIRC-05). |
| 5 | `message_queue` | `MessageQueue` | **Tenant-scoped** | Очередь. Композитный индекс `(workspace_id, sender_id, status)` пригодится для Phase 3+. |
| 6 | `conversations` | `Conversation` | **Tenant-scoped** | Inbox по workspace (INBX-01). |
| 7 | `warmup_pool` | `WarmupPool` | **Tenant-scoped (D-03)** | Warmup только внутри workspace. |
| 8 | `warmup_sessions` | `WarmupSession` | **Tenant-scoped (D-03)** | Аналогично. |
| 9 | `warmup_messages` | `WarmupMessage` | **Tenant-scoped (D-03)** | Аналогично. Или можно вывести через `from_sender_id.workspace_id` — но `NOT NULL` прямой колонкой надёжнее. |
| 10 | `proxy_pool` | `ProxyPool` | **Tenant-scoped (D-03)** | BYO-proxy. |
| 11 | `context_contact_assignments` | `ContextContactAssignment` | **Tenant-scoped** | Можно вывести через `context_id.workspace_id`, но прямой `workspace_id` нужен для query "найди все assignments workspace без JOIN". |

**Всего:** 11 таблиц `ADD COLUMN workspace_id`. Все 11 уже существуют в ORM-моделях, все будут созданы при первом `Base.metadata.create_all` запуске на пустой БД, после чего миграция 012 добавит колонки.

**Важная нота для planner'а:** Поскольку БД пустая (D-01), при первом запуске возможны два сценария:

- **Сценарий A:** `init_db()` создаёт ВСЕ таблицы из ORM, после этого 012 ALTER-ит каждую → `ADD COLUMN IF NOT EXISTS workspace_id NOT NULL` на пустой таблице → OK без backfill.
- **Сценарий B (если planner решит убрать `create_all` в C-04):** 012 должна сама создать **все 11 таблиц** raw SQL-ом → значительно длиннее миграция. Не рекомендую этот путь в Phase 1.

**Рекомендация:** Сохранить `Base.metadata.create_all` в `init_db()` на Phase 1 (Scenario A), оставить TODO про migration runner на Phase 5+. Это минимизирует scope и совпадает с прецедентом из миграции 011.

### Composite Indexes (для будущих фаз)

Анализ `.where(...)` запросов в существующих роутерах/сервисах (см. grep вывод в Step 2):

| Текущий запрос (в Phase 2-4 станет workspace-scoped) | Нужный composite index в 012 |
|------------------------------------------------------|------------------------------|
| `Sender.slug == X` (uses `slug` unique idx) | `senders(workspace_id, slug)` — будет уникальным в рамках workspace, но в Phase 1 оставить slug глобально-уникальным; Phase 2 переделает |
| `MessageQueue.id == X` (PK) | Не нужно |
| `MessageQueue.status IN ('pending', 'processing')` (worker tick) | Существующий частичный индекс `idx_message_queue_sender_status_scheduled` остаётся; Phase 3+ может добавить workspace |
| `ProxyPool.assigned_to_sender_id IS NULL` (free pool) | `proxy_pool(workspace_id, assigned_to_sender_id) WHERE assigned_to_sender_id IS NULL` — partial composite |
| `Conversation.sender_id == X AND status == Y` | `conversations(workspace_id, sender_id, status)` — для Phase 4 inbox |
| `ContactCache.phone == X` | `contacts_cache(workspace_id, phone)` — для Phase 3 |

**Рекомендация Phase 1:** Добавить **только базовые** `idx_<table>_workspace` (BTREE на `workspace_id` каждой таблицы). Composite indexes — задача Phase 2-4 как часть рерайта роутеров.

**Обоснование:** Сейчас БД пустая. Создание индексов на пустых таблицах быстрое. Но избыточный composite index — лишний maintenance overhead на INSERT. Ставим только то, что будем использовать в первой же query → это plain `workspace_id` индекс для WHERE-фильтра.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Хеширование API-ключей | Свой SHA-256/HMAC | `bcrypt.hashpw` / `bcrypt.checkpw` | Адаптивная сложность, salt, constant-time compare |
| JWT decode/verify | Свой парсер base64 + HMAC | `jose.jwt.decode` (или PyJWT) | Кросс-валидация `aud`, `iss`, `exp`, корректная обработка clock skew |
| Token generation | `random.choice(alphabet)` | `secrets.token_urlsafe(32)` | Cryptographically secure, url-safe base64 |
| Prefix lookup для API-ключей | Хранить plaintext, искать LIKE | Хранить `prefix VARCHAR(12)` + индекс + bcrypt-verify над кандидатами | Plaintext = leak risk; LIKE без индекса = O(n) |
| Workspace auto-create | If-check + 2 separate transactions | `async with db.begin():` — один atomic block | Race condition между двумя одновременными первыми запросами от одного пользователя |
| UUID generation | `random.randint` | `uuid.uuid4()` (Python) или `gen_random_uuid()` (Postgres) | Соответствует существующему паттерну, collision-resistant |
| HTTP middleware для auth | Свой `BaseHTTPMiddleware` | `Depends(auth_dep)` per-router | FastAPI идиоматика; middleware теряет инъекцию контекста в хендлеры |

**Key insight:** Phase 1 — это в чистом виде "клей" между уже-готовыми библиотеками. Любой код, который выглядит как "я сам напишу tiny version of X" — красный флаг.

---

## Common Pitfalls

### Pitfall 1: Supabase HS256 + asymmetric migration timeline

**Что происходит:** Supabase с октября 2025 default-но создаёт новые проекты с ES256/JWKS, а не HS256. Legacy "JWT Secret" в Dashboard → Settings → API всё ещё доступен (можно вручную переключиться на HS256), но **новые проекты могут его не иметь сразу видимым** — нужно явно использовать legacy режим.

**Почему это важно для Phase 1:** D-05 предписывает HS256 + local validation. Если пользователь создал Supabase проект до октября 2025 — всё работает. Если создал после — могут увидеть ES256 default, отсутствие "JWT Secret" поля, и Phase 1 backend сломается при первом запросе.

**Как избежать:**
1. **В Plan 01-02** (auth middleware) явный шаг: документировать в `DOCS.md` или `.env.example` процедуру получения `SUPABASE_JWT_SECRET` — "Settings → API → JWT Settings → Legacy JWT Secret. Если не видно — переключиться на legacy mode".
2. В коде `_decode_jwt` ловить `JWTError` с конкретным сообщением "signing method ... is invalid" → возвращать осмысленный 500 с TODO про ES256 migration.
3. Оставить TODO-маркер в `auth.py`: `# TODO(v2): migrate to ES256/JWKS — see https://supabase.com/blog/jwt-signing-keys`.

**Warning signs:** В Supabase Dashboard в Settings → API нет поля "JWT Secret" — значит проект на signing keys только. Нужно перевести в legacy или мигрировать backend на ES256 (вне scope Phase 1).

### Pitfall 2: python-jose deprecated

**Что происходит:** `python-jose==3.3.0` — последний релиз с 2022-06. FastAPI официально мигрировал docs на PyJWT в discussion #11345 (2024). Несколько CVE были обнаружены в python-jose и не залаплены.

**Почему важно:** Используем для HS256 декода JWT-токенов. Для HS256 риск низкий — алгоритм простой, известных эксплойтов в python-jose именно для HS256 нет на 2026-05.

**Как избежать:** Либо оставить python-jose (рекомендую для Phase 1 — D-05 locked, минимизируем delta), либо параллельно добавить PyJWT и использовать его (decision planner'а; нужно тогда обновить D-05 — но это уже nitpick).

**Warning signs:** Snyk advisor / Github Dependabot выдаст алерт на `python-jose 3.3.0`. Это **information-only** для Phase 1 — не блокер.

### Pitfall 3: bcrypt sync блокирует event loop

**Что происходит:** `bcrypt.checkpw(plaintext, hash)` — синхронный CPU-bound вызов, занимает ~80-300ms на cost=12. Если вызвать прямо в `async def auth_dep`, event loop блокируется на это время. При нескольких параллельных запросах с API-ключом — деградация throughput.

**Почему важно:** Каждый запрос с `X-Workspace-Key` идёт через bcrypt verify. n8n может слать пачки по 100 запросов параллельно.

**Как избежать:**
```python
loop = asyncio.get_event_loop()
match = await loop.run_in_executor(None, bcrypt.checkpw, plaintext.encode(), hash.encode())
# или короче:
match = await asyncio.to_thread(bcrypt.checkpw, plaintext.encode(), hash.encode())
```

**Warning signs:** Profiling показывает высокую latency на `auth_dep` под нагрузкой; uvicorn worker uses 100% CPU при низком RPS.

### Pitfall 4: `Base.metadata.create_all` + raw SQL migrations конфликт

**Что происходит:** `app/database.py:38` вызывает `Base.metadata.create_all` при старте API. SQLAlchemy создаёт **все** таблицы из ORM (включая Postgres TYPE для `SQLEnum(MessageType)` и т.д.). Если миграция 012 пытается тоже создать ту же таблицу — `CREATE TABLE IF NOT EXISTS` спасает. Но если миграция меняет колонку, которая уже создана `create_all` с другим определением — divergence.

**Конкретный риск Phase 1:** Миграция 012 добавляет `ADD COLUMN workspace_id UUID NOT NULL`. ORM-модель тоже получит `workspace_id = Column(UUID, ForeignKey, nullable=False)`. На пустой таблице это OK. Но если planner забудет добавить колонку в ORM-модель, а только в SQL — `create_all` пропустит её, при следующем insert через ORM будут ошибки.

**Как избежать:**
1. **Обязательно** обновить ORM-модели в `app/models/__init__.py` синхронно с миграцией 012.
2. Записать правило в `CLAUDE.md` (или в DOCS.md): "Любая миграция, добавляющая колонку, должна одновременно обновить ORM-модель".
3. **C-04 решение:** оставить `create_all` для Phase 1 (минимизируем delta), но добавить TODO про migration runner на Phase 5+.

**Warning signs:** SQLAlchemy NOT NULL violation на INSERT, хотя колонка есть в БД.

### Pitfall 5: Workspace creation race condition

**Что происходит:** D-08 — lazy auto-create. Если два запроса от **одного** пользователя приходят одновременно (например Lovable шлёт `GET /workspace` и `POST /auth/me` параллельно при первом логине), оба могут пройти JWT-decode, оба не найти `user_workspaces` row, оба создать новый `workspace` → пользователь получит **два** workspace.

**Как избежать:**
1. Все операции с `user_workspaces` — внутри `async with db.begin():` транзакции.
2. После транзакции — повторный SELECT с тем же `supabase_user_id` чтобы понять "это я создал или другой запрос успел?".
3. Альтернатива (проще): на `user_workspaces` добавить условный UNIQUE: `CREATE UNIQUE INDEX uq_user_workspaces_one_per_user ON user_workspaces(supabase_user_id) WHERE role = 'owner'`. Тогда вторая параллельная INSERT упадёт с UniqueViolation, ловим и делаем SELECT.

**Но это противоречит D-10** (явно "без UNIQUE"). Поэтому **рекомендую вариант 1+2**: атомарная транзакция + post-commit SELECT.

**Warning signs:** В БД появляются дублирующие `workspaces` rows для одного `supabase_user_id`.

### Pitfall 6: API-key prefix collision

**Что происходит:** D-13 prefix VARCHAR(12). `secrets.token_urlsafe(32)` даёт ~43 base64-url символа. Первые 8 символов (если C-02 = 8) — это 48 бит энтропии, коллизия возможна на ~16M ключах. Первые 12 — 72 бит, безопасно до триллионов.

**Рекомендация planner'у:** **12-символьный prefix.** `wsk_` + 8 url-safe символов из 32-byte random. Итого визуальная длина: `wsk_aBcDeFgH` = 12 знаков prefix (включая `wsk_`).

Точная формула:
```python
raw = secrets.token_urlsafe(32)         # ~43 chars
token = f"wsk_{raw}"                    # полный токен (хранится только у клиента)
prefix = token[:12]                     # 'wsk_' + первые 8 случайных = 12 chars
bcrypt_hash = bcrypt.hashpw(token.encode(), bcrypt.gensalt())
# В БД: prefix=prefix, bcrypt_hash=bcrypt_hash
```

**Lookup:**
```python
prefix = received_token[:12]
candidates = SELECT * FROM workspace_api_keys WHERE prefix = $1 AND revoked_at IS NULL
for c in candidates:
    if bcrypt.checkpw(received_token, c.bcrypt_hash):
        return c
```

Обычно candidates = 1 row. Несколько ключей с одинаковым prefix маловероятно (1 из 16M на 100 ключах workspace).

### Pitfall 7: `messages` table в migration 001 — не в ORM

**Что нашлось:** `migrations/001_add_unique_constraint_messages.sql` оперирует таблицей `messages`, но в `app/models/__init__.py` нет такой модели (есть `MessageLog` → `messages_log`). Это инасследие от telegram-api — таблица `messages` создаётся где-то ещё (вероятно в listener-флоу) и не отражена в текущих ORM-моделях.

**Решение для Phase 1:** **Не трогать** таблицу `messages`. Не добавлять `workspace_id` к ней в миграции 012. Если в будущем (Phase 4 inbox) `MessageLog`/`messages_log` будет покрывать функционал — отлично. Если же `messages` нужна и она tenant-scoped — это всплывёт в Phase 4 как отдельная миграция 013.

**Warning signs:** При запуске на свежей БД listener начнёт писать в таблицу `messages` (мы её не создавали), упадёт. Решение: листенер не трогаем в Phase 1 (D-15), запускаем только API-контейнер.

---

## Runtime State Inventory

Phase 1 — **частично рефакторинг с переименованием** (заменяем X-API-Key → AuthCtx) + добавление новых таблиц. Runtime state важен.

| Категория | Найдено | Действие |
|-----------|---------|----------|
| **Stored data** | БД пустая (D-01). Старые prod-данные в `/root/apps/telegram-api/` остаются как есть, **outreach-platform создаёт свой Postgres-контейнер** (см. docker-compose). | Никакой data migration не требуется. Backfill `workspace_id` — невозможен и не нужен. |
| **Live service config** | Lovable frontend (внешний SaaS) — нужно обновить env var с URL backend. SUPABASE_URL, SUPABASE_ANON_KEY на frontend — вне scope Phase 1. n8n workflows — пока не используются, будут перенастроены в Phase 3. | После деплоя Phase 1: задокументировать в DOCS.md какие env vars Lovable должен установить (`VITE_API_URL`, `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`). |
| **OS-registered state** | Нет cron-jobs, нет systemd units, нет Windows Task Scheduler. Деплой через `docker compose up`. **Но:** `container_name: telegram-api-db` в docker-compose.yml — **коллизия со старым prod на том же VPS!** | **Critical:** Plan 01-01 (миграция) должен включить task "rename container_name в docker-compose.yml: `telegram-api-db` → `outreach-platform-db`, `telegram-api` → `outreach-platform-api`, `telegram-listener` → `outreach-platform-listener`". Иначе `docker compose up` на VPS убъёт старый prod telegram-api. |
| **Secrets / env vars** | Текущий `.env` имеет `API_KEY=...`. После Phase 1 — **`API_KEY` не используется API-сервисом**, но **остаётся используется в listener** (`docker-compose.yml` секция listener → `API_KEY: ${API_KEY}`). | **Не удалять** `API_KEY` из listener env. Удалить только из `api` секции в docker-compose. Из `app/config.py` — удалить поле `api_key`. **Добавить** в `.env`: `SUPABASE_JWT_SECRET=...`, `SUPABASE_URL=...`, `CORS_ALLOWED_ORIGINS=https://app.outreach-platform.com,http://localhost:5173`. |
| **Build artifacts / installed packages** | `bcrypt` не в requirements.txt → новый `pip install`. После добавления в requirements.txt: `docker compose up -d --build api` пересоберёт образ. Listener не пересобираем (он не использует bcrypt). | Plan включить шаг: "Add `bcrypt>=4.1.0,<5.0` to requirements.txt; rebuild API container". |

**Canonical question check:** *После того как все файлы в репо обновлены, что в runtime ещё держит старое состояние?*

→ **Docker контейнеры со старыми именами** (если planner забудет переименовать в docker-compose.yml). **Env var `API_KEY`** в `.env` на VPS — остаётся, но больше не читается API. **Lovable env vars** — отдельная задача deployment.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|-------------|-----------|---------|----------|
| Docker + Docker Compose | Деплой | ✓ предполагается | — | — |
| Python 3.11+ | API/listener runtime | ✓ через Dockerfile | 3.11 | — |
| Postgres 16 | БД | ✓ через docker-compose | 16 | — |
| `gen_random_uuid()` function | Миграция 012 | ✓ встроена в Postgres 13+ | — | `uuid_generate_v4()` + CREATE EXTENSION uuid-ossp |
| **Supabase project с legacy JWT Secret** | AUTH-03 | ⚠️ depends on project age (см. Pitfall 1) | — | Manual switch в Supabase Dashboard → legacy mode |
| **Lovable frontend with Supabase Auth wired** | AUTH-01/02/04 | ⚠️ Phase 1 backend не зависит от него (валидирует уже выданный JWT), но без него тестировать вручную нужно через `curl` с заранее полученным JWT | — | Использовать `curl -H "Authorization: Bearer <token>"` с токеном, полученным через Supabase REST API напрямую для smoke-теста |
| **bcrypt** | API-key verify | ✗ нет в requirements.txt | — | Нет — обязателен, planner должен добавить |
| pytest / pytest-asyncio | Тесты (см. Validation Architecture) | ✗ нет в requirements.txt и в коде нет ни одного теста | — | Установить как dev-dependency: `pytest>=8.0`, `pytest-asyncio>=0.23`, `httpx` (уже есть) для TestClient |

**Missing dependencies with no fallback:**
- **bcrypt** — необходим для D-13 (API key verification).
- **pytest, pytest-asyncio** — для Validation Architecture (nyquist_validation=true).

**Missing dependencies with fallback:**
- Supabase project legacy JWT Secret — если не доступен, fallback = manual switch в Dashboard или переход на ES256/JWKS (вне Phase 1 scope).

---

## Code Examples

Проверенные паттерны (источники указаны).

### Example 1: JWT decode с Supabase

```python
# Источник: https://dev.to/zwx00/validating-a-supabase-jwt-locally-with-python-and-fastapi-59jf
#           https://python-jose.readthedocs.io/en/latest/jwt/

from jose import jwt, JWTError, ExpiredSignatureError, JWTClaimsError

def _decode_supabase_jwt(token: str) -> dict:
    """Decode + verify Supabase JWT. Returns claims dict or raises HTTPException(401)."""
    try:
        claims = jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",            # Supabase default audience
            options={"require": ["sub", "exp"]}, # sub = supabase_user_id
        )
    except ExpiredSignatureError:
        raise HTTPException(401, detail={"code": "TOKEN_EXPIRED", "message": "JWT expired"})
    except JWTClaimsError as e:
        raise HTTPException(401, detail={"code": "TOKEN_INVALID_CLAIMS", "message": str(e)})
    except JWTError as e:
        raise HTTPException(401, detail={"code": "TOKEN_INVALID", "message": "Invalid JWT"})
    return claims
```

### Example 2: Dual-auth dependency (полный псевдокод)

```python
# Источник: composite — D-11/D-12 из CONTEXT.md + FastAPI Depends idiom

async def auth_dep(
    authorization: Optional[str] = Header(None),
    x_workspace_key: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
) -> AuthCtx:
    # Branch 1: Bearer JWT
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
        claims = _decode_supabase_jwt(token)        # raises 401 on bad
        supabase_user_id = claims["sub"]
        email = claims.get("email")
        ctx = await _resolve_or_create_workspace(db, supabase_user_id, email)
        return ctx

    # Branch 2: Workspace API key
    if x_workspace_key and x_workspace_key.startswith("wsk_"):
        ctx = await _verify_api_key(db, x_workspace_key)  # raises 401 if invalid
        return ctx

    # No credentials
    raise HTTPException(401, detail={"code": "AUTH_REQUIRED",
                                     "message": "Provide Authorization Bearer or X-Workspace-Key"})


async def _resolve_or_create_workspace(
    db: AsyncSession, supabase_user_id: str, email: str | None
) -> AuthCtx:
    # Find existing
    result = await db.execute(
        select(UserWorkspace).where(UserWorkspace.supabase_user_id == supabase_user_id)
    )
    uw = result.scalars().first()

    if uw is not None:
        return AuthCtx(
            workspace_id=uw.workspace_id,
            user_id=supabase_user_id,
            source="jwt",
            role=uw.role,
        )

    # Auto-create (D-08): atomic transaction
    workspace_name = email or "My Workspace"
    async with db.begin():
        workspace = Workspace(name=workspace_name)
        db.add(workspace)
        await db.flush()                           # получаем workspace.id
        uw = UserWorkspace(
            supabase_user_id=supabase_user_id,
            workspace_id=workspace.id,
            role="owner",
        )
        db.add(uw)
        # commit на выходе из async with

    return AuthCtx(
        workspace_id=workspace.id,
        user_id=supabase_user_id,
        source="jwt",
        role="owner",
    )


async def _verify_api_key(db: AsyncSession, raw_token: str) -> AuthCtx:
    prefix = raw_token[:12]
    result = await db.execute(
        select(WorkspaceApiKey).where(
            WorkspaceApiKey.prefix == prefix,
            WorkspaceApiKey.revoked_at.is_(None),
        )
    )
    candidates = result.scalars().all()

    for c in candidates:
        # bcrypt — sync, run в thread pool (Pitfall 3)
        ok = await asyncio.to_thread(
            bcrypt.checkpw, raw_token.encode(), c.bcrypt_hash.encode()
        )
        if ok:
            # update last_used_at (best-effort, не блокируем)
            c.last_used_at = func.now()
            await db.commit()
            return AuthCtx(
                workspace_id=c.workspace_id,
                user_id=None,
                source="api_key",
                role=None,
            )

    raise HTTPException(401, detail={"code": "API_KEY_INVALID",
                                     "message": "Invalid or revoked workspace key"})
```

### Example 3: API key generation endpoint

```python
# Источник: composite — D-13 + bcrypt docs

@router.post("/workspace/api-keys", response_model=ApiKeyCreateResponse)
async def create_api_key(
    request: ApiKeyCreateRequest,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    raw_secret = secrets.token_urlsafe(32)
    full_token = f"wsk_{raw_secret}"
    prefix = full_token[:12]
    bcrypt_hash = await asyncio.to_thread(
        bcrypt.hashpw, full_token.encode(), bcrypt.gensalt()
    )

    key = WorkspaceApiKey(
        workspace_id=ctx.workspace_id,
        prefix=prefix,
        bcrypt_hash=bcrypt_hash.decode(),
        name=request.name,
    )
    db.add(key)
    await db.commit()
    await db.refresh(key)

    return ApiKeyCreateResponse(
        id=key.id,
        prefix=key.prefix,
        name=key.name,
        token=full_token,             # ВИДЕН ТОЛЬКО ЗДЕСЬ. Никогда больше.
        created_at=key.created_at,
    )
```

### Example 4: Migration 012 skeleton

См. секцию "Architecture Patterns → Pattern 1" выше.

---

## Endpoint Skeleton (C-03 resolved)

Минимально-достаточный набор endpoint-ов для Phase 1 Success Criteria:

| Method | Path | Auth | Purpose | Phase Req |
|--------|------|------|---------|-----------|
| `POST` | `/api/v1/auth/me` | JWT only | Bootstrap: первый запрос Lovable после login. Триггерит auto-create workspace. Возвращает текущий `AuthCtx` + workspace. | TENT-02, AUTH-03 |
| `GET` | `/api/v1/workspace` | JWT or API key | Возвращает текущий workspace (id, name, created_at). | TENT-04 |
| `PATCH` | `/api/v1/workspace` | JWT only (role=owner) | Переименование workspace (после auto-create по email). | TENT-04 |
| `POST` | `/api/v1/workspace/api-keys` | JWT only (role=owner) | Создать новый `wsk_` ключ. Возвращает plaintext **один раз**. | TENT-03 |
| `GET` | `/api/v1/workspace/api-keys` | JWT only | Список ключей текущего workspace (id, prefix, name, last_used_at, revoked_at). Без plaintext. | TENT-03 |
| `DELETE` | `/api/v1/workspace/api-keys/{id}` | JWT only (role=owner) | Soft-delete: `revoked_at = NOW()`. | TENT-03 |
| `GET` | `/api/v1/health` | None (no auth) | Liveness. Уже существует. | — |

**Замечание:** Эндпоинты с "role=owner" в Phase 1 могут просто проверять `ctx.source == 'jwt'` (не API key). Точная role-проверка (owner vs admin vs member) — задача Phase 5+ team support. В Phase 1 любой залогиненный owner-инвариант: все JWT-пользователи являются owner своего workspace (D-10 v1).

**Из scope (отложено):**
- `POST /api/v1/workspace/api-keys/{id}/regenerate` — D-13 говорит "регенерация = revoke + create new", т.е. два запроса. Не нужен отдельный эндпоинт.
- `POST /api/v1/auth/signup` — Supabase делает signup сам, backend не участвует.
- Endpoint смены active workspace — отложено в v2.

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact для Phase 1 |
|--------------|------------------|--------------|---------------------|
| Single `X-API-Key` header (telegram-api inheritance) | Dual auth: JWT (UI) + `X-Workspace-Key` per-workspace | Phase 1 (this work) | Старый удаляется полностью (D-14) |
| `python-jose` (FastAPI tutorial 2019-2023) | PyJWT (FastAPI tutorial 2024+) | discussion #11345 | **Не меняем в Phase 1** (D-05 — locked). Отложить на v2 если надо. |
| `passlib[bcrypt]` (FastAPI tutorial classic) | `bcrypt` напрямую, или `pwdlib` | FastAPI discussion #11773, 2024 | **Используем bcrypt напрямую** (passlib unmaintained, ломается на Py3.13) |
| Supabase HS256 JWT Secret | Supabase ES256 + JWKS (new projects default) | October 2025 | См. Pitfall 1 — Phase 1 остаётся на HS256, но v2 миграция запланирована |
| Postgres RLS для tenant isolation | Application-level filter (`get_db_scoped`) | 2010s стандарт vs. modern multi-tenant SaaS | D-04: RLS отложен на v2. Phase 1 — app-level only. |
| SQLAlchemy `create_all` для bootstrap | Migration runner (Alembic/raw) | 2015 vs. now | C-04 — оставить `create_all` в Phase 1 как pragmatic compromise |

**Deprecated/outdated (на 2026-05):**
- `passlib` — последний commit ~2020, ломается на Python 3.13
- `python-jose` — последний релиз 2022-06; для HS256 работает, но **не рекомендуется для новых проектов**

---

## Open Questions

### 1. C-04: что делать с `Base.metadata.create_all`?

**Что известно:** Существующий код вызывает `create_all` в `init_db()`. Это работает на пустой БД для первичной структуры, но конфликтует с migration-runner подходом. CONCERNS.md документирует это как tech debt.

**Что неясно:** Стоит ли в Phase 1 запилить настоящий migration runner (читает `migrations/*.sql` и применяет по очереди в одной транзакции)?

**Recommendation:** **Оставить `create_all` в Phase 1**, не трогать. Добавить TODO-комментарий "deprecated, replace with migration runner in v2". Причины:
1. Минимизация scope Phase 1 (D-15 запрещает трогать services; database.py не упомянут в discretion).
2. БД пустая → `create_all` работает корректно (создаст все ORM-таблицы), потом 012 ALTER-ит.
3. Заменить на runner — это ~50 строк кода + риск багов в первичном bootstrap.

Если planner всё же решит фиксить — это отдельный Plan (например 01-04: migration runner), не вписывать в 01-01.

### 2. C-02: 8 vs 12 символов prefix?

**Что известно:** `secrets.token_urlsafe(32)` → ~43 base64-url знаков → высокая энтропия.

**Что решено в этом research:** **12 символов** (включая `wsk_` префикс) — это 8 случайных знаков ≈ 48 бит, безопасно до 16M ключей. Удобно: уместный размер для индекса, видим в логах без обрезания.

### 3. Postgres TYPE для SQLEnum — нужно ли в миграции?

**Что неясно:** Существующий код использует `SQLEnum(MessageType)` для нескольких полей. `Base.metadata.create_all` создаёт Postgres TYPE автоматически. Если planner решит для `UserWorkspace.role` использовать SQLEnum (а не VARCHAR+CHECK), то raw SQL миграция 012 должна явно `CREATE TYPE user_workspace_role AS ENUM (...)`.

**Recommendation:** Использовать **VARCHAR(20) + CHECK constraint** в `user_workspaces.role`, повторив паттерн `senders.role` и `senders.auth_status` (миграции 003, 011). На Python-стороне опционально — Python `enum.Enum` + конверсия в string при insert. Это сохраняет миграцию 012 простой и идемпотентной.

### 4. Что делать с `app/routers/auth.py` — удалить файл или оставить пустым?

**Recommendation:** **Удалить файл целиком.** Все импорты `from app.routers.auth import verify_api_key` в старых роутерах — те роутеры тоже отключаются из main.py (D-14), они станут dead code. Plan 01-03 удаляет файл, в Phase 2 при рерайте `senders.py` будут уже использовать `app.utils.auth.auth_dep`.

---

## Validation Architecture

**Detection:** В проекте **нет** тестового фреймворка. `pytest`, `pytest-asyncio` не установлены, директории `tests/` или `test/` нет.

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 8.x + pytest-asyncio 0.23+ (Wave 0 — установить) |
| Config file | `pyproject.toml` (создать в Wave 0) с секциями `[tool.pytest.ini_options]` и `asyncio_mode = "auto"` |
| Quick run command | `pytest tests/ -x --tb=short` |
| Full suite command | `pytest tests/ -v` |
| Async HTTP test client | `httpx.AsyncClient` (httpx уже в requirements.txt) + `ASGITransport(app=app)` для in-process |
| Test database | Отдельный Postgres docker-контейнер или `pytest-docker` / опционально SQLite-in-memory для unit (не подходит — мы используем asyncpg + JSONB) → **рекомендую отдельный test Postgres container** |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|--------------|
| TENT-01 | `workspace_id` обязателен на всех 11 таблицах (DB constraint) | unit (SQL) | `pytest tests/test_migration_012.py -x` | ❌ Wave 0 |
| TENT-02 | Первый JWT-запрос создаёт workspace + user_workspaces | integration | `pytest tests/test_auth_dep.py::test_lazy_workspace_create -x` | ❌ Wave 0 |
| TENT-03 | POST /workspace/api-keys возвращает plaintext token один раз, GET — никогда | integration | `pytest tests/test_workspace_api_keys.py -x` | ❌ Wave 0 |
| TENT-04 | Запрос без auth → 401; запрос с невалидным JWT → 401; запрос с revoked key → 401 | integration | `pytest tests/test_auth_dep.py::test_no_auth_rejected -x` | ❌ Wave 0 |
| AUTH-01/02/04 | Supabase magic-link flow | manual-only | (тест через Lovable UI — не автоматизируем backend-side) | manual |
| AUTH-03 | JWT decode HS256, audience=authenticated, sub→user_workspaces lookup | unit | `pytest tests/test_auth_dep.py::test_decode_jwt -x` | ❌ Wave 0 |

### Sampling Rate

- **Per task commit:** `pytest tests/ -x --tb=short` (~10s на in-memory unit tests)
- **Per wave merge:** `pytest tests/ -v` (полный прогон)
- **Phase gate:** Полный прогон зелёный + manual smoke: `curl -H "Authorization: Bearer <real-supabase-token>" http://localhost:8000/api/v1/auth/me` возвращает 200 с workspace_id.

### Wave 0 Gaps

- [ ] `pyproject.toml` создать с конфигом pytest (или `pytest.ini` если planner предпочитает)
- [ ] `tests/__init__.py` создать
- [ ] `tests/conftest.py` — фикстуры:
  - `async_db_session` — изолированная async session с rollback после теста
  - `test_client` — `AsyncClient` с `ASGITransport(app=app)`
  - `valid_jwt` — фикстура генерирует валидный JWT, подписанный тестовым `SUPABASE_JWT_SECRET`
  - `workspace_fixture` — создаёт workspace + user_workspace для аутентифицированного юзера
- [ ] `tests/test_migration_012.py` — covers TENT-01 (структура БД после миграции)
- [ ] `tests/test_auth_dep.py` — covers TENT-02, TENT-04, AUTH-03 (auth flow)
- [ ] `tests/test_workspace_api_keys.py` — covers TENT-03 (CRUD api-keys)
- [ ] Установка фреймворка: добавить в requirements.txt (dev section, или отдельный `requirements-dev.txt`):
  ```
  pytest>=8.0
  pytest-asyncio>=0.23
  pytest-postgresql>=5.0       # для управления тестовым Postgres
  # или используем существующий docker-compose db + помечаем тест маркером
  ```

**Замечание:** Учитывая полное отсутствие тестов в кодбейсе на сегодня, Wave 0 фактически — это создание test infrastructure с нуля. Это значительный объём работы, но критически нужен для Phase 1, потому что:
1. auth-логика — security-critical, баги попадают в продакшен невидимыми
2. без тестов невозможно безопасно переписывать роутеры в Phase 2-4 поверх workspace_id

Если planner решит **отложить тесты в v2** (т.е. отключить `workflow.nyquist_validation` для Phase 1) — это допустимый pragmatic trade-off, но **должно быть зафиксировано** в PLAN.md как явное технологическое решение, с описанием рисков.

---

## Sources

### Primary (HIGH confidence — официальные docs или код проекта)

- `app/main.py`, `app/routers/auth.py`, `app/config.py`, `app/database.py`, `app/models/__init__.py` — текущая структура кода
- `migrations/001` через `011` — паттерны raw SQL (BEGIN/COMMIT, IF NOT EXISTS, gen_random_uuid)
- `requirements.txt` — установленные зависимости
- `.planning/codebase/ARCHITECTURE.md`, `STRUCTURE.md`, `INTEGRATIONS.md`, `CONCERNS.md`, `CONVENTIONS.md` — codebase intel
- `.planning/phases/01-workspace-foundation/01-CONTEXT.md` — locked decisions D-01..D-15
- `CLAUDE.md` — проектные правила
- [PyJWT decode API](https://pyjwt.readthedocs.io/en/stable/usage.html) — exact function signatures, exceptions, options.require pattern
- [bcrypt PyPI](https://pypi.org/project/bcrypt/) — verified bcrypt 4.x is active

### Secondary (MEDIUM confidence — независимые источники, кросс-проверенные)

- [Supabase JWT Signing Keys announcement](https://supabase.com/blog/jwt-signing-keys) — ES256 migration timeline (Oct 2025)
- [Supabase Self-Hosting Auth Keys docs](https://supabase.com/docs/guides/self-hosting/self-hosted-auth-keys) — legacy vs. new signing keys
- [Validating Supabase JWT with FastAPI (dev.to)](https://dev.to/zwx00/validating-a-supabase-jwt-locally-with-python-and-fastapi-59jf) — `audience='authenticated'` подтверждён
- [Migrating Supabase JWT to JWKS (objectgraph.com)](https://objectgraph.com/blog/migrating-supabase-jwt-jwks/) — timeline и dashboard locations
- [FastAPI discussion #11345](https://github.com/fastapi/fastapi/discussions/11345) — официальный отказ от python-jose в пользу PyJWT
- [FastAPI discussion #11773](https://github.com/fastapi/fastapi/discussions/11773) — passlib замена

### Tertiary (LOW confidence — стоит проверить если будет нужно)

- Точная версия `python-jose` "последнего релиза" — не верифицировано через PyPI live (взято из обсуждений 2024)
- bcrypt `cost=12` стандарт — общепринят, но конкретная latency (80-300ms) — общая прикидка, не профайл на этой машине

---

## Metadata

**Confidence breakdown:**

- **Standard Stack:** HIGH — все библиотеки уже в проекте либо легко добавляются; версии проверены по requirements.txt
- **Architecture Patterns:** HIGH — паттерны взяты из существующих файлов (миграций 001-011, существующих роутеров, ORM-моделей)
- **Pitfalls 1 (Supabase ES256):** MEDIUM — таймлайн взят из публичных Supabase blog, но конкретное поведение dashboard для нового проекта в мае 2026 не проверено вживую
- **Pitfalls 2-7 (python-jose, bcrypt, create_all, race, prefix, messages-table):** HIGH — каждый верифицирован через code inspection или official docs
- **Tenant-Scoped Tables Inventory:** HIGH — grep по `app/models/__init__.py` исчерпывающий
- **Validation Architecture:** HIGH — текущее состояние "нет тестов" подтверждено `ls tests/` (не существует) и отсутствием pytest в requirements

**Research date:** 2026-05-21
**Valid until:** 2026-06-21 (Supabase JWT signing keys migration ongoing — стоит ре-верифицировать раз в месяц до v2)
