---
phase: 01-workspace-foundation
plan: 03
type: execute
wave: 3
depends_on:
  - "01-01"
  - "01-02"
files_modified:
  - app/config.py
  - app/main.py
  - app/routers/workspace.py
  - app/routers/auth.py
  - docker-compose.yml
  - tests/test_workspace_router.py
  - tests/test_workspace_api_keys.py
autonomous: true
requirements:
  - AUTH-01
  - AUTH-04
  - TENT-03
user_setup:
  - service: supabase
    why: "Issue valid HS256 JWT for /api/v1/auth/me smoke test"
    env_vars:
      - name: SUPABASE_URL
        source: "Supabase Dashboard → Project Settings → API → Project URL"
      - name: CORS_ALLOWED_ORIGINS
        source: "Comma-separated list of Lovable frontend domains (e.g. http://localhost:5173,https://app.outreach-platform.com)"
must_haves:
  truths:
    - "POST /api/v1/auth/me с валидным JWT возвращает 200 с workspace_id (bootstrap — TENT-02 + AUTH-01 UX)"
    - "GET /api/v1/workspace возвращает данные текущего workspace (JWT или API key)"
    - "PATCH /api/v1/workspace переименовывает workspace (только JWT, owner-инвариант)"
    - "POST /api/v1/workspace/api-keys создаёт wsk_ ключ; plaintext возвращается ровно один раз"
    - "GET /api/v1/workspace/api-keys возвращает список БЕЗ plaintext — только prefix, name, timestamps"
    - "DELETE /api/v1/workspace/api-keys/{id} soft-revokes (revoked_at = NOW), cross-tenant 404"
    - "Старый verify_api_key удалён; файл app/routers/auth.py удалён"
    - "Из app/main.py выпилены 10 старых include_router (выживает только health + новый workspace) — D-14"
    - "CORS ограничен до cors_allowed_origins из settings (не allow_origins=['*'])"
    - "app/config.py содержит supabase_jwt_secret, supabase_url, cors_allowed_origins; api_key удалён"
    - "Supabase env vars прокинуты в docker-compose.yml api-секцию"
    - "AUTH-04 (refresh) работает на уровне backend (stateless, JWT валидируется на каждом запросе)"
  artifacts:
    - path: "app/routers/workspace.py"
      provides: "CRUD endpoints для workspace + workspace_api_keys"
      exports: ["router"]
    - path: "app/main.py"
      provides: "Очищенный entry point: только health + workspace роутеры, CORS lockdown"
      contains: "include_router(workspace.router)"
    - path: "app/config.py"
      provides: "Settings с Supabase + CORS полями; api_key удалён"
      contains: "supabase_jwt_secret"
    - path: "tests/test_workspace_router.py"
      provides: "Integration tests: GET/PATCH /workspace, /auth/me bootstrap"
      contains: "test_auth_me_bootstrap"
    - path: "tests/test_workspace_api_keys.py"
      provides: "Integration tests: POST/GET/DELETE /workspace/api-keys, plaintext-once"
      contains: "test_create_api_key_returns_plaintext_once"
  key_links:
    - from: "app/main.py"
      to: "app/routers/workspace.py:router"
      via: "app.include_router(workspace.router)"
      pattern: "include_router\\(workspace\\.router\\)"
    - from: "app/routers/workspace.py"
      to: "app/utils/auth.py:auth_dep"
      via: "ctx: AuthCtx = Depends(auth_dep) во всех endpoint-ах"
      pattern: "Depends\\(auth_dep\\)"
    - from: "app/routers/workspace.py:create_api_key"
      to: "ApiKeyCreateResponse.token field"
      via: "plaintext-once: full_token возвращается только в POST-ответе, в GET — никогда (TENT-03 enforcement)"
      pattern: "token=full_token"
---

<objective>
Финальный план Phase 1: построить workspace router (6 endpoints — `POST /auth/me`, `GET/PATCH /workspace`, `POST/GET/DELETE /workspace/api-keys/...`), вычистить `app/main.py` от 10 старых `include_router` (D-14), удалить старый `app/routers/auth.py` (verify_api_key), обновить `app/config.py` (добавить Supabase + CORS, удалить api_key) и завершить docker-compose env vars. Покрытие AUTH-01 (UX magic link через bootstrap endpoint), AUTH-04 (refresh — backend stateless), TENT-03 (workspace API ключи).

Этот план — последняя миля Phase 1: после его завершения продукт принимает Supabase JWT через UI и `wsk_` ключи через n8n, любой запрос без auth → 401, все 10+ старых бизнес-эндпоинтов выпилены (они будут переписаны поверх workspace_id в Phase 2-4).

Purpose: Без этих endpoints клиент не может ни увидеть свой workspace, ни создать API-ключ для n8n. Без cleanup main.py — старые routers продолжают принимать X-API-Key, что ломает изоляцию и противоречит D-14.
Output: Рабочий API-скелет, готовый к расширению в Phase 2-4; зелёные integration-тесты через HTTP; чистый main.py.
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
@/Users/andrewbruce/Documents/outreach-platform/.planning/phases/01-workspace-foundation/01-02-SUMMARY.md

# Канонические аналоги
@/Users/andrewbruce/Documents/outreach-platform/app/routers/contexts.py
@/Users/andrewbruce/Documents/outreach-platform/app/routers/senders.py
@/Users/andrewbruce/Documents/outreach-platform/app/routers/health.py
@/Users/andrewbruce/Documents/outreach-platform/app/routers/auth.py
@/Users/andrewbruce/Documents/outreach-platform/app/main.py
@/Users/andrewbruce/Documents/outreach-platform/app/config.py
@/Users/andrewbruce/Documents/outreach-platform/docker-compose.yml
@/Users/andrewbruce/Documents/outreach-platform/app/utils/auth.py

<interfaces>
<!-- AuthCtx и auth_dep (созданы в плане 01-02): -->
class AuthCtx(BaseModel):
    workspace_id: UUID
    user_id: Optional[str]
    source: Literal["jwt", "api_key"]
    role: Optional[str]

async def auth_dep(authorization, x_workspace_key, db) -> AuthCtx

<!-- Endpoint skeleton (RESEARCH §Endpoint Skeleton, C-03 resolved): -->
POST /api/v1/auth/me            — JWT only, bootstrap, триггерит auto-create
GET  /api/v1/workspace          — JWT or API key
PATCH /api/v1/workspace         — JWT only (source check)
POST /api/v1/workspace/api-keys — JWT only, возвращает plaintext ОДИН раз
GET  /api/v1/workspace/api-keys — JWT only, без plaintext
DELETE /api/v1/workspace/api-keys/{id} — JWT only, soft-revoke

<!-- ORM модели (созданы в 01-01): -->
class Workspace(Base):
    id, name, created_at, updated_at

class WorkspaceApiKey(Base):
    id, workspace_id, prefix, bcrypt_hash, name,
    created_at, last_used_at, revoked_at
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Обновить app/config.py + удалить app/routers/auth.py</name>
  <files>app/config.py, app/routers/auth.py</files>
  <read_first>
    - /Users/andrewbruce/Documents/outreach-platform/app/config.py (текущая структура Settings — какие поля, какой формат)
    - /Users/andrewbruce/Documents/outreach-platform/app/routers/auth.py (файл, который удаляем — 25 строк, verify_api_key)
    - /Users/andrewbruce/Documents/outreach-platform/.planning/phases/01-workspace-foundation/01-PATTERNS.md (§6 — config diff; §5 — main.py cleanup)
    - /Users/andrewbruce/Documents/outreach-platform/.planning/phases/01-workspace-foundation/01-RESEARCH.md (§Open Questions §4 — почему удаляем auth.py; §Runtime State Inventory — что делать с api_key в listener; Pitfall — listener тоже использует config)
    - /Users/andrewbruce/Documents/outreach-platform/.planning/phases/01-workspace-foundation/01-CONTEXT.md (D-14 — старые роутеры выпиливаются; D-15 — services не трогаем)
    - /Users/andrewbruce/Documents/outreach-platform/.planning/codebase/CONVENTIONS.md (§Module Design — Settings + lru_cache)
  </read_first>
  <action>
**Часть A — обновить `app/config.py`**

Прочитать текущий файл. Выполнить точные изменения в классе `Settings`:

1. **Удалить поле** `api_key: str` (D-14: больше не используется API-сервисом).
2. **Добавить поля** в секцию `# Security` (после `encryption_key`):
   ```python
       # Supabase (Phase 1)
       supabase_jwt_secret: str
       supabase_url: str
       # CORS (Phase 1)
       cors_allowed_origins: str = "http://localhost:5173"
   ```
3. **Добавить property** для парсинга CORS origins из comma-separated string:
   ```python
       @property
       def cors_origins_list(self) -> list[str]:
           """Парсит CORS_ALLOWED_ORIGINS в list для FastAPI CORSMiddleware."""
           return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]
   ```

**Landmine (Pitfall в RESEARCH §Runtime State Inventory):** если в плане 01-02 Task 2 поле `supabase_jwt_secret` уже добавлено (defensive), убедиться что:
- Оно НЕ дублируется (один раз в файле)
- Тип точно `str` (не `Optional[str]`)
- Расположено в секции `# Supabase (Phase 1)`

**Важно про listener:** Listener (`app/services/listener.py`) использует тот же класс `Settings`. После удаления `api_key` listener может упасть при импорте если код где-то ссылается на `settings.api_key`. Проверить grep по `settings.api_key` в `app/services/`:

```bash
grep -rn "settings.api_key" app/services/
```

Если найдены reference — пометить как явный риск (создать TODO-комментарий в коде, не править — D-15 запрещает трогать services). НО: если код просто импортирует и использует — Phase 1 принимает риск что listener при перезапуске упадёт; user-setup в frontmatter указал что listener нужно НЕ ПЕРЕСОБИРАТЬ в Phase 1 (D-15).

**Альтернативное решение** (если grep показал что services читают settings.api_key и проверка через `make config` валится):
- Временно оставить `api_key: Optional[str] = None` в Settings до Phase 2 рерайта.
- Это противоречит D-14 буквально, но соответствует D-15 (services не трогаем).
- **Discretion planner-а: выбрать второй вариант, если grep что-то найдёт** — задокументировать в комментарии: `# TODO(phase-2): remove api_key after services migration`.

**Часть B — удалить файл `app/routers/auth.py`**

Удалить весь файл (RESEARCH §Open Questions §4 — удалить целиком, не оставлять пустым). Команда:

```bash
rm app/routers/auth.py
```

**ВАЖНО:** В этом плане 01-03 Task 2 (main.py) будут удалены все импорты `from app.routers.auth import verify_api_key`. Если plan executor выполнит Task 1 РАНЬШЕ Task 2 — `python3 -c "from app.main import app"` упадёт с ImportError. Это OK: Task 2 идёт сразу после, и финальная проверка делается после обоих.

**Также важно:** Старые роутеры (`senders.py`, `contexts.py`, etc.) импортируют `verify_api_key` тоже. После удаления — они станут "broken imports". Это OK потому что в Task 2 они выпиливаются из `main.py` через `include_router` (D-14: файлы оставляем, импорты в main.py удаляем). Сами `app/routers/senders.py` etc. остаются как "dead code файлы" — они не загружаются Python interpreter, пока их никто не импортирует.

**НО:** Если в `app/main.py` где-то ещё импортируется `from app.routers.auth import verify_api_key` напрямую (а не через `from app.routers import senders` который импортирует через senders) — это сразу ломается. Проверить:

```bash
grep -rn "from app.routers.auth" app/main.py
```

Обычно `main.py` НЕ импортирует `verify_api_key` напрямую — это делают только бизнес-роутеры. Но грепнуть стоит.

**Что НЕ делать:**
- НЕ удалять `app/routers/senders.py`, `app/routers/contexts.py` и другие старые роутеры (D-14: файлы оставляем).
- НЕ менять `app/routers/health.py` — он выживает.
- НЕ трогать `app/services/` (D-15).
  </action>
  <verify>
    <automated>
test -f app/config.py && \
! grep -E "^\s*api_key:\s*str\s*$" app/config.py && \
grep -q "supabase_jwt_secret: str" app/config.py && \
grep -q "supabase_url: str" app/config.py && \
grep -q "cors_allowed_origins" app/config.py && \
grep -q "cors_origins_list" app/config.py && \
! test -f app/routers/auth.py && \
echo "checking config import works..." && \
SUPABASE_JWT_SECRET=test SUPABASE_URL=http://test DATABASE_URL=postgresql+asyncpg://test ENCRYPTION_KEY=test TELEGRAM_API_ID=1 TELEGRAM_API_HASH=test python3 -c "from app.config import get_settings; s = get_settings(); assert s.supabase_jwt_secret == 'test'; assert isinstance(s.cors_origins_list, list)" 2>&1
    </automated>
  </verify>
  <acceptance_criteria>
- `app/config.py` НЕ содержит строку `api_key: str` (или содержит `Optional[str] = None` с TODO-маркером — допустимо только если grep по `app/services/` показал явное использование)
- `app/config.py` содержит ровно один экземпляр `supabase_jwt_secret: str`
- `app/config.py` содержит `supabase_url: str` 
- `app/config.py` содержит `cors_allowed_origins: str = "http://localhost:5173"`
- `app/config.py` содержит `@property def cors_origins_list(self) -> list[str]` парсер
- Файл `app/routers/auth.py` НЕ существует (rm выполнен)
- Импорт `from app.config import get_settings` работает с минимальным env: достаточно `SUPABASE_JWT_SECRET=test SUPABASE_URL=http://test DATABASE_URL=... ENCRYPTION_KEY=... TELEGRAM_API_ID=... TELEGRAM_API_HASH=...`
- НЕ удалены другие файлы — `ls app/routers/` показывает все старые файлы кроме `auth.py`
- `app/routers/health.py` существует и не изменён
  </acceptance_criteria>
  <done>
Config очищен: Supabase + CORS добавлены, api_key удалён (или с TODO marker). Старый auth.py удалён как файл — больше нет verify_api_key в проекте.
  </done>
</task>

<task type="auto">
  <name>Task 2: Создать app/routers/workspace.py + перерайтировать app/main.py + докер env</name>
  <files>app/routers/workspace.py, app/main.py, docker-compose.yml</files>
  <read_first>
    - /Users/andrewbruce/Documents/outreach-platform/app/routers/contexts.py (lines 1-260 — canonical CRUD pattern: header, inline schemas, GET list, POST create, PATCH partial update, DELETE soft-style)
    - /Users/andrewbruce/Documents/outreach-platform/app/routers/senders.py (lines 67-105 — POST + db.refresh pattern)
    - /Users/andrewbruce/Documents/outreach-platform/app/routers/health.py (минимальный пример живого роутера)
    - /Users/andrewbruce/Documents/outreach-platform/app/main.py (lines 11-14: импорты роутеров; 26-46: lifespan; 56-63: CORS; 66-75: include_router) — что выпилить
    - /Users/andrewbruce/Documents/outreach-platform/app/utils/auth.py (импорт auth_dep + AuthCtx)
    - /Users/andrewbruce/Documents/outreach-platform/app/models/__init__.py (Workspace, WorkspaceApiKey — для ORM-запросов)
    - /Users/andrewbruce/Documents/outreach-platform/.planning/phases/01-workspace-foundation/01-PATTERNS.md (§4 — workspace.py паттерн; §5 — main.py cleanup; §8 — docker-compose env)
    - /Users/andrewbruce/Documents/outreach-platform/.planning/phases/01-workspace-foundation/01-RESEARCH.md (§Endpoint Skeleton — точный список 6 endpoints; §Code Examples 3 — create_api_key пример; Pitfall 3, 6 — bcrypt + prefix)
    - /Users/andrewbruce/Documents/outreach-platform/.planning/phases/01-workspace-foundation/01-CONTEXT.md (D-13 — формат wsk_ ключа; D-14 — что выпиливаем; C-03 — endpoint list)
  </read_first>
  <action>
**Часть A — создать `app/routers/workspace.py`** (~250-300 строк по образцу contexts.py)

Полная структура с 6 endpoints. Шаблон строго следует PATTERNS.md §4:

```python
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
from app.models import Workspace, WorkspaceApiKey
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
    )
    workspace = result.scalars().first()
    if workspace is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "WORKSPACE_NOT_FOUND", "message": "Workspace not found"},
        )

    return WorkspaceResponse(
        id=workspace.id,
        name=workspace.name,
        created_at=workspace.created_at,
        updated_at=workspace.updated_at,
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

    return WorkspaceResponse(
        id=workspace.id,
        name=workspace.name,
        created_at=workspace.created_at,
        updated_at=workspace.updated_at,
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
```

**Конвенции (из PATTERNS.md §4):**
- `router = APIRouter(prefix="/api/v1", tags=["workspace"])` — БЕЗ `/workspace` в prefix (т.к. есть `/auth/me`). Endpoint paths начинаются с `/auth/me` или `/workspace`.
- Inline Pydantic schemas внутри файла (повторяя contexts.py паттерн)
- `ctx: AuthCtx = Depends(auth_dep)` во всех endpoint-ах
- ORM-стиль `.execute(select(...).where(...))` вместо `text("...")` raw SQL — это NEW код, тип-безопасность приоритетнее (PATTERNS.md §4 Pattern C "Альтернатива")
- `bcrypt.hashpw` обёрнут в `asyncio.to_thread` (Pitfall 3)
- TODO-маркеры `# TODO(v2-rls)` на каждом workspace_id фильтре

**Часть B — перерайтировать `app/main.py`**

Прочитать текущий файл. Заменить целиком (минимум удалений):

1. **Удалить импорты** (line 11-14):
   ```python
   from app.routers import send, senders, health, conversations, contexts, onboarding, check_contacts
   from app.routers import queue as queue_router
   from app.routers import warmup as warmup_router
   from app.routers import proxy_pool as proxy_pool_router
   ```

2. **Заменить на**:
   ```python
   from app.routers import health, workspace
   ```

3. **Удалить `app.include_router(...)`** для всех старых роутеров (line 66-75), кроме `health`:
   ```python
   # БЫЛО — 10 include_router
   # СТАЛО:
   app.include_router(health.router)
   app.include_router(workspace.router)
   ```

4. **Заменить CORS** (line 56-63):
   ```python
   # БЫЛО:
   app.add_middleware(
       CORSMiddleware,
       allow_origins=["*"],
       ...
   )
   # СТАЛО:
   app.add_middleware(
       CORSMiddleware,
       allow_origins=settings.cors_origins_list,
       allow_credentials=True,
       allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
       allow_headers=["Authorization", "X-Workspace-Key", "Content-Type"],
   )
   ```
   Импортировать `settings`: `from app.config import get_settings` + `settings = get_settings()` на module level.

5. **Lifespan** (line 26-46) — оставить AS IS. `queue_worker.start()` и `warmup_worker.start()` остаются (D-15 — services не трогаем). На пустой БД они не делают ничего (пустые очереди).

**Что НЕ делать:**
- НЕ удалять lifespan (D-15).
- НЕ удалять `from app.database import init_db, engine`.
- НЕ удалять root-route `/` если он есть.

**Часть C — финализировать `docker-compose.yml`** (env vars)

Добавить в `services.api.environment` (плана 01-01 уже переименовал контейнеры):

```yaml
      SUPABASE_JWT_SECRET: ${SUPABASE_JWT_SECRET}
      SUPABASE_URL: ${SUPABASE_URL}
      CORS_ALLOWED_ORIGINS: ${CORS_ALLOWED_ORIGINS}
```

Удалить из `services.api.environment`:
```yaml
      API_KEY: ${API_KEY}    # ← УДАЛИТЬ из api секции (D-14)
```

**Сохранить** `API_KEY: ${API_KEY}` в `services.listener.environment` — listener ещё его использует (D-15: services не трогаем).

**Что НЕ делать в docker-compose:**
- НЕ удалять `OPENAI_API_KEY`, `TELEGRAM_API_*`, `ENCRYPTION_KEY` — они нужны.
- НЕ менять `volumes`, `networks`, `depends_on`.
  </action>
  <verify>
    <automated>
test -f app/routers/workspace.py && \
grep -q "class AuthCtx" app/utils/auth.py && \
grep -q "@router.post(\"/auth/me\"" app/routers/workspace.py && \
grep -q "@router.get(\"/workspace\"" app/routers/workspace.py && \
grep -q "@router.patch(\"/workspace\"" app/routers/workspace.py && \
grep -q "@router.post(" app/routers/workspace.py && grep -q "/workspace/api-keys" app/routers/workspace.py && \
grep -q "@router.get(\"/workspace/api-keys\"" app/routers/workspace.py && \
grep -q "@router.delete(\"/workspace/api-keys/{key_id}\"" app/routers/workspace.py && \
grep -q "token=full_token" app/routers/workspace.py && \
grep -q "asyncio.to_thread" app/routers/workspace.py && \
grep -q "WorkspaceApiKey.workspace_id == ctx.workspace_id" app/routers/workspace.py && \
grep -q "TODO(v2-rls)" app/routers/workspace.py && \
[ "$(grep -c 'include_router' app/main.py)" = "2" ] && \
grep -q "include_router(workspace.router)" app/main.py && \
grep -q "include_router(health.router)" app/main.py && \
! grep -q "allow_origins=\[\"\\*\"\]" app/main.py && \
grep -q "settings.cors_origins_list" app/main.py && \
grep -q "SUPABASE_JWT_SECRET:" docker-compose.yml && \
grep -q "SUPABASE_URL:" docker-compose.yml && \
grep -q "CORS_ALLOWED_ORIGINS:" docker-compose.yml && \
docker compose config -q 2>&1
    </automated>
  </verify>
  <acceptance_criteria>
- `app/routers/workspace.py` существует, содержит:
  - 6 endpoints: `POST /auth/me`, `GET /workspace`, `PATCH /workspace`, `POST /workspace/api-keys`, `GET /workspace/api-keys`, `DELETE /workspace/api-keys/{key_id}`
  - Inline schemas: `AuthMeResponse`, `WorkspaceResponse`, `WorkspaceUpdate`, `ApiKeyCreateRequest`, `ApiKeyCreateResponse`, `ApiKeyListItem`, `ApiKeyListResponse`
  - `_require_jwt(ctx)` helper для JWT-only endpoints
  - `ctx: AuthCtx = Depends(auth_dep)` во всех endpoint-ах (`grep -c "Depends(auth_dep)" app/routers/workspace.py` ≥ 6)
  - `asyncio.to_thread(bcrypt.hashpw, ...)` — bcrypt async wrap
  - `token=full_token` ровно в одном месте (ApiKeyCreateResponse) — plaintext-once
  - `WorkspaceApiKey.workspace_id == ctx.workspace_id` — cross-tenant guard в DELETE и LIST
  - Минимум 3 `TODO(v2-rls)` маркера
- `app/main.py`:
  - Содержит ровно 2 `include_router` вызова (health + workspace) — `grep -c "include_router" app/main.py` == 2
  - Не содержит `allow_origins=["*"]` 
  - Содержит `settings.cors_origins_list` в CORS-секции
  - Импорт `from app.routers import health, workspace`
  - НЕ импортирует `send, senders, conversations, contexts, onboarding, check_contacts, queue, warmup, proxy_pool`
  - НЕ импортирует `from app.routers.auth import verify_api_key`
- `docker-compose.yml`:
  - Содержит `SUPABASE_JWT_SECRET: ${SUPABASE_JWT_SECRET}` в `api` секции
  - Содержит `SUPABASE_URL: ${SUPABASE_URL}` в `api` секции
  - Содержит `CORS_ALLOWED_ORIGINS: ${CORS_ALLOWED_ORIGINS}` в `api` секции
  - НЕ содержит `API_KEY: ${API_KEY}` в `api` секции (но содержит в `listener` секции)
  - `docker compose config -q` exit 0
- Python импорт целиком работает: `python3 -c "from app.main import app; from app.routers.workspace import router"` exit 0
  </acceptance_criteria>
  <done>
Скелет API готов: 6 endpoints зарегистрированы в FastAPI; main.py содержит только health + workspace; CORS lockdown; env vars в docker-compose; docker compose config валиден.
  </done>
</task>

<task type="auto">
  <name>Task 3: Тесты — workspace router endpoints (test_workspace_router.py + test_workspace_api_keys.py)</name>
  <files>tests/test_workspace_router.py, tests/test_workspace_api_keys.py</files>
  <read_first>
    - /Users/andrewbruce/Documents/outreach-platform/tests/conftest.py (фикстуры из плана 01-02: async_client, valid_supabase_jwt, async_db_session)
    - /Users/andrewbruce/Documents/outreach-platform/tests/test_auth_dep.py (паттерны из плана 01-02 для assert)
    - /Users/andrewbruce/Documents/outreach-platform/app/routers/workspace.py (Task 2 — точные пути и формы ответов)
    - /Users/andrewbruce/Documents/outreach-platform/.planning/phases/01-workspace-foundation/01-RESEARCH.md (§Validation Architecture Pattern C — endpoint integration test; §Endpoint Skeleton — auth матрица)
    - /Users/andrewbruce/Documents/outreach-platform/app/models/__init__.py (для прямых INSERT в тестах если нужны seed-данные)
  </read_first>
  <action>
**Часть A — создать `tests/test_workspace_router.py`**

Integration тесты через `async_client` фикстуру (httpx + ASGITransport).

```python
"""
Integration tests для workspace endpoints (AUTH-01 bootstrap, AUTH-04 refresh, TENT-04).

POST /api/v1/auth/me
GET  /api/v1/workspace
PATCH /api/v1/workspace
"""

import pytest


# ─── POST /auth/me (TENT-02 + AUTH-01 bootstrap UX) ──────────────────────────

async def test_auth_me_no_auth_returns_401(async_client):
    """Без заголовков → 401."""
    response = await async_client.post("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "AUTH_REQUIRED"


async def test_auth_me_bootstrap_creates_workspace(async_client, valid_supabase_jwt):
    """Первый вызов с валидным JWT → создаётся workspace, возвращается id."""
    token = valid_supabase_jwt(sub="me-test-user-1", email="me@example.com")
    response = await async_client.post(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert "workspace_id" in body
    assert body["user_id"] == "me-test-user-1"
    assert body["source"] == "jwt"
    assert body["role"] == "owner"
    assert body["workspace_name"] == "me@example.com"  # D-09


async def test_auth_me_idempotent(async_client, valid_supabase_jwt):
    """Повторный вызов с тем же sub → тот же workspace_id."""
    token = valid_supabase_jwt(sub="me-test-user-2", email="me2@example.com")
    r1 = await async_client.post(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    r2 = await async_client.post(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["workspace_id"] == r2.json()["workspace_id"]


async def test_auth_me_rejects_api_key(async_client):
    """auth/me JWT-only (D-10): попытка с X-Workspace-Key → 401 (нет валидного ключа без bootstrap)."""
    response = await async_client.post(
        "/api/v1/auth/me",
        headers={"X-Workspace-Key": "wsk_random_invalid"},
    )
    assert response.status_code == 401


# ─── GET /workspace ──────────────────────────────────────────────────────────

async def test_get_workspace_with_jwt(async_client, valid_supabase_jwt):
    """JWT даёт доступ к GET /workspace."""
    token = valid_supabase_jwt(sub="get-user-1", email="get1@example.com")
    response = await async_client.get(
        "/api/v1/workspace",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert "id" in body
    assert body["name"] == "get1@example.com"


async def test_get_workspace_no_auth_401(async_client):
    """Без заголовков → 401."""
    response = await async_client.get("/api/v1/workspace")
    assert response.status_code == 401


# ─── PATCH /workspace (rename) ───────────────────────────────────────────────

async def test_patch_workspace_renames(async_client, valid_supabase_jwt):
    """JWT → rename работает."""
    token = valid_supabase_jwt(sub="patch-user-1", email="patch1@example.com")
    # Bootstrap
    await async_client.post(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    # Rename
    response = await async_client.patch(
        "/api/v1/workspace",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "My Renamed Workspace"},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "My Renamed Workspace"


async def test_patch_workspace_empty_name_400(async_client, valid_supabase_jwt):
    """Empty name → 400 INVALID_NAME."""
    token = valid_supabase_jwt(sub="patch-user-2", email="patch2@example.com")
    response = await async_client.patch(
        "/api/v1/workspace",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "   "},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_NAME"
```

**Часть B — создать `tests/test_workspace_api_keys.py`**

Integration тесты для CRUD api-keys (TENT-03).

```python
"""
Integration tests для workspace API-keys (TENT-03).

POST /workspace/api-keys — plaintext возвращается ОДИН раз
GET  /workspace/api-keys — без plaintext
DELETE /workspace/api-keys/{id} — soft-revoke
Cross-tenant: ключ workspace A невидим для workspace B.
"""

import pytest


async def test_create_api_key_returns_plaintext_once(async_client, valid_supabase_jwt):
    """POST возвращает plaintext token (начинается с wsk_)."""
    token = valid_supabase_jwt(sub="apikey-user-1", email="api1@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    response = await async_client.post(
        "/api/v1/workspace/api-keys",
        headers=headers,
        json={"name": "n8n-integration"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["token"].startswith("wsk_")
    assert len(body["prefix"]) == 12  # wsk_ + 8 chars
    assert body["prefix"] == body["token"][:12]
    assert body["name"] == "n8n-integration"


async def test_list_api_keys_excludes_plaintext(async_client, valid_supabase_jwt):
    """GET НЕ должен возвращать plaintext token (только prefix)."""
    token = valid_supabase_jwt(sub="apikey-user-2", email="api2@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    # Создаём ключ
    create = await async_client.post(
        "/api/v1/workspace/api-keys",
        headers=headers,
        json={"name": "first-key"},
    )
    assert create.status_code == 201

    # Запрашиваем список
    list_response = await async_client.get(
        "/api/v1/workspace/api-keys", headers=headers
    )
    assert list_response.status_code == 200
    body = list_response.json()
    assert body["total"] >= 1
    for item in body["api_keys"]:
        # Никаких полей с plaintext
        assert "token" not in item
        assert "bcrypt_hash" not in item
        assert item["prefix"].startswith("wsk_")


async def test_revoke_api_key(async_client, valid_supabase_jwt):
    """DELETE → revoked_at заполнено; GET показывает revoked_at."""
    token = valid_supabase_jwt(sub="apikey-user-3", email="api3@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    create = await async_client.post(
        "/api/v1/workspace/api-keys",
        headers=headers,
        json={"name": "to-revoke"},
    )
    key_id = create.json()["id"]

    revoke = await async_client.delete(
        f"/api/v1/workspace/api-keys/{key_id}", headers=headers
    )
    assert revoke.status_code == 204

    list_response = await async_client.get(
        "/api/v1/workspace/api-keys", headers=headers
    )
    revoked_key = next(
        k for k in list_response.json()["api_keys"] if k["id"] == key_id
    )
    assert revoked_key["revoked_at"] is not None


async def test_revoked_key_cannot_authenticate(async_client, valid_supabase_jwt):
    """Revoked ключ → 401 при попытке использовать."""
    token = valid_supabase_jwt(sub="apikey-user-4", email="api4@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    create = await async_client.post(
        "/api/v1/workspace/api-keys",
        headers=headers,
        json={"name": "will-be-revoked"},
    )
    full_token = create.json()["token"]
    key_id = create.json()["id"]

    # Revoke
    await async_client.delete(
        f"/api/v1/workspace/api-keys/{key_id}", headers=headers
    )

    # Используем revoked ключ
    attempt = await async_client.get(
        "/api/v1/workspace",
        headers={"X-Workspace-Key": full_token},
    )
    assert attempt.status_code == 401
    assert attempt.json()["detail"]["code"] == "API_KEY_INVALID"


async def test_api_key_grants_access_to_workspace_endpoint(
    async_client, valid_supabase_jwt
):
    """Валидный wsk_ ключ даёт доступ к GET /workspace (TENT-04)."""
    token = valid_supabase_jwt(sub="apikey-user-5", email="api5@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    create = await async_client.post(
        "/api/v1/workspace/api-keys",
        headers=headers,
        json={"name": "n8n-prod"},
    )
    full_token = create.json()["token"]

    # Используем ключ для GET /workspace
    response = await async_client.get(
        "/api/v1/workspace",
        headers={"X-Workspace-Key": full_token},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "api5@example.com"


async def test_cross_tenant_isolation(async_client, valid_supabase_jwt):
    """Ключ workspace A нельзя удалить через JWT workspace B (404, не 403 — security)."""
    token_a = valid_supabase_jwt(sub="apikey-iso-A", email="iso-a@example.com")
    token_b = valid_supabase_jwt(sub="apikey-iso-B", email="iso-b@example.com")

    # A создаёт ключ
    create = await async_client.post(
        "/api/v1/workspace/api-keys",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"name": "a-only"},
    )
    a_key_id = create.json()["id"]

    # B пробует удалить ключ A
    delete = await async_client.delete(
        f"/api/v1/workspace/api-keys/{a_key_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert delete.status_code == 404  # not found, NOT 403 (security: hide existence)

    # A видит свой ключ нетронутым
    list_a = await async_client.get(
        "/api/v1/workspace/api-keys",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    a_key = next(k for k in list_a.json()["api_keys"] if k["id"] == a_key_id)
    assert a_key["revoked_at"] is None
```

**Конвенции:**
- `async def test_*` функции
- httpx использует `async_client` фикстуру из conftest.py
- Каждый тест использует уникальный `sub` для изоляции workspace (избегаем cross-test state)
- bcrypt в тестах использует ДЕФОЛТ rounds (production behavior — checkpw здесь нужен realistic).

**Что НЕ делать:**
- НЕ менять conftest.py (создан в 01-02).
- НЕ тестировать listener / queue worker — out of scope.
- НЕ использовать `requests` (sync) — только `async_client`.
  </action>
  <verify>
    <automated>
test -f tests/test_workspace_router.py && \
test -f tests/test_workspace_api_keys.py && \
grep -q "test_auth_me_bootstrap_creates_workspace" tests/test_workspace_router.py && \
grep -q "test_auth_me_idempotent" tests/test_workspace_router.py && \
grep -q "test_patch_workspace_renames" tests/test_workspace_router.py && \
grep -q "test_create_api_key_returns_plaintext_once" tests/test_workspace_api_keys.py && \
grep -q "test_list_api_keys_excludes_plaintext" tests/test_workspace_api_keys.py && \
grep -q "test_revoke_api_key" tests/test_workspace_api_keys.py && \
grep -q "test_revoked_key_cannot_authenticate" tests/test_workspace_api_keys.py && \
grep -q "test_cross_tenant_isolation" tests/test_workspace_api_keys.py && \
grep -q "test_api_key_grants_access_to_workspace_endpoint" tests/test_workspace_api_keys.py && \
docker compose up -d db && sleep 3 && \
pytest tests/test_workspace_router.py tests/test_workspace_api_keys.py tests/test_auth_dep.py tests/test_migration_012.py -v --tb=short 2>&1
    </automated>
  </verify>
  <acceptance_criteria>
- `tests/test_workspace_router.py` существует, содержит минимум 7 тестов:
  - `test_auth_me_no_auth_returns_401`
  - `test_auth_me_bootstrap_creates_workspace` (TENT-02 + AUTH-01 UX)
  - `test_auth_me_idempotent` (повторный вызов = тот же workspace_id)
  - `test_auth_me_rejects_api_key` (JWT-only)
  - `test_get_workspace_with_jwt`
  - `test_get_workspace_no_auth_401`
  - `test_patch_workspace_renames`
  - `test_patch_workspace_empty_name_400`
- `tests/test_workspace_api_keys.py` существует, содержит минимум 6 тестов:
  - `test_create_api_key_returns_plaintext_once` (D-13 — wsk_ префикс, length 12)
  - `test_list_api_keys_excludes_plaintext` (security: НЕТ полей token/bcrypt_hash в ответе)
  - `test_revoke_api_key`
  - `test_revoked_key_cannot_authenticate` (revoked_at IS NOT NULL → 401)
  - `test_api_key_grants_access_to_workspace_endpoint` (TENT-04: api-key даёт workspace_id)
  - `test_cross_tenant_isolation` (D-04: 404 не 403 — security hide existence)
- Все тесты используют `async_client` и `valid_supabase_jwt` фикстуры
- Полный прогон `pytest tests/ -v` exit 0: ВСЕ тесты Phase 1 (migration_012 + auth_dep + workspace_router + workspace_api_keys) зелёные
- Каждый тест использует уникальный `sub=` (нет cross-test state)
- Прогон pytest даёт >= 25 passed (8 router + 6 api-keys + 9 auth_dep + 25+ migration_012 параметризованных)
  </acceptance_criteria>
  <done>
HTTP-уровень полностью покрыт: bootstrap, rename, api-keys CRUD, cross-tenant isolation, plaintext-once enforcement, revoke. Все 4 тестовых модуля Phase 1 зелёные.
  </done>
</task>

</tasks>

<verification>
**Phase 1 финальная верификация (после 01-03):**

1. **Static-проверки cleanup:**
   - `grep -c "include_router" app/main.py` == 2 (только health + workspace)
   - `test ! -f app/routers/auth.py` (старый файл удалён)
   - `grep "allow_origins=\[\"\\*\"\]" app/main.py` пусто (CORS lockdown)
   - `grep "^api_key:" app/config.py` пусто (или есть только Optional с TODO)

2. **Smoke полного API:**
   ```bash
   docker compose up -d --build api
   sleep 5
   curl -sf http://localhost:8000/api/v1/health   # 200 OK
   curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/v1/workspace   # 401 (no auth)
   ```

3. **End-to-end test suite:**
   ```bash
   pytest tests/ -v
   # Все тесты зелёные:
   # - tests/test_migration_012.py — 25+ тестов (параметризация по 11 таблицам)
   # - tests/test_auth_dep.py — 9 тестов
   # - tests/test_workspace_router.py — 8 тестов
   # - tests/test_workspace_api_keys.py — 6 тестов
   ```

4. **Manual smoke с реальным Supabase JWT** (requires env vars):
   ```bash
   # User получает JWT через Supabase REST API напрямую или через Lovable, копирует в TOKEN
   curl -X POST -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/auth/me
   # → 200 с workspace_id, workspace_name=email
   ```
</verification>

<success_criteria>
- [ ] app/config.py: supabase_jwt_secret, supabase_url, cors_allowed_origins, cors_origins_list property добавлены; api_key удалён (или Optional с TODO)
- [ ] app/routers/auth.py удалён (rm)
- [ ] app/routers/workspace.py с 6 endpoints; plaintext-once enforcement; cross-tenant guard; bcrypt async
- [ ] app/main.py: 2 include_router (health + workspace); CORS = settings.cors_origins_list; 10 старых импортов удалены
- [ ] docker-compose.yml: SUPABASE_JWT_SECRET, SUPABASE_URL, CORS_ALLOWED_ORIGINS в api; API_KEY оставлен в listener
- [ ] tests/test_workspace_router.py (8+ тестов) и tests/test_workspace_api_keys.py (6+ тестов) зелёные
- [ ] Полный pytest exit 0 на всех тестах Phase 1
- [ ] Phase 1 Success Criteria #1: магия magic link — backend принимает Supabase JWT (через `/auth/me`)
- [ ] Phase 1 Success Criteria #2: workspace auto-create при первом JWT-запросе (TENT-02)
- [ ] Phase 1 Success Criteria #3: 401 без auth (test_get_workspace_no_auth_401)
- [ ] Phase 1 Success Criteria #4: workspace API-ключ виден в /workspace/api-keys
- [ ] Phase 1 Success Criteria #5: все 11 tenant-scoped таблиц имеют workspace_id NOT NULL (TENT-01)
</success_criteria>

<output>
После завершения создать файл `.planning/phases/01-workspace-foundation/01-03-SUMMARY.md` с описанием:
- 6 endpoints workspace router + конкретные пути
- Что выпилено из app/main.py (список 9 удалённых include_router)
- Что в config (новые поля + удалённые)
- Какие env vars теперь обязательны (`SUPABASE_JWT_SECRET`, `SUPABASE_URL`, `CORS_ALLOWED_ORIGINS`)
- Покрытие тестами по requirement-ам (TENT-03, AUTH-01, AUTH-04 + Phase 1 Success Criteria mapping)
- Готовность к Phase 2 (TG Accounts & Contacts) — что заложено, какие routers осталось переписать

Также обновить `.planning/STATE.md`:
- progress.completed_phases: 1
- progress.completed_plans: 3
- progress.percent: вычислить (3 из 21)
- Current Position: Phase: 2 of 6
- last_activity: дата + краткое описание

Также обновить `.planning/ROADMAP.md`:
- Phase 1 Plans отметить как `- [x]` (3 чекбокса)
- В Progress таблице: Phase 1 → `3/3`, Status `Completed`, Completed `2026-XX-XX`
</output>
