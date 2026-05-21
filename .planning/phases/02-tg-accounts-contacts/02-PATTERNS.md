# Phase 2: TG Accounts & Contacts — Pattern Map

**Mapped:** 2026-05-21
**Files analyzed:** 17 (создаются + переписываются + расширяются)
**Analogs found:** 17 / 17 (полное покрытие — Phase 1 + унаследованный код дают аналоги для всего)

---

## File Classification

### Создаются с нуля

| Новый файл | Роль | Data Flow | Ближайший аналог | Match Quality |
|------------|------|-----------|------------------|---------------|
| `migrations/013_phase2.sql` | migration | DDL (CREATE/ALTER) | `migrations/012_workspace.sql` | exact (тот же стиль raw SQL + BEGIN/COMMIT + `IF NOT EXISTS`) |
| `app/utils/phone.py` | utility | pure-transform (string → string) | `app/services/encryption.py` (singular utility helper) | role-match (stateless pure functions, stdlib only) |
| `app/routers/folders.py` | router | request-response CRUD | `app/routers/workspace.py` (workspace-scoped CRUD на AuthDep) | exact (то же: workspace-isolation, JWT+API key, `Depends(auth_dep)`) |
| `app/routers/contacts.py` | router | request-response + multipart upload | `app/routers/workspace.py` + `app/routers/check_contacts.py` (Pydantic schemas + Sender lookup) | role-match (CRUD + multipart требует UploadFile) |
| `app/services/contact_check_worker.py` | service / background worker | event-driven (poll DB → batch resolve) | `app/services/queue.py::QueueWorker` (background asyncio task + `start/stop` в lifespan) + `app/services/warmup.py::WarmupWorker` (TICK_INTERVAL + `_tick`) | exact (полный паттерн worker'а уже есть в проекте) |
| `app/services/csv_import.py` | service | transform (bytes → dict) | `app/services/encryption.py` (модуль чистых хелперов без worker'а) | role-match (нет CSV-аналога; используется stdlib `csv`/`io`) |
| `app/services/onboarding_state.py` | service | CRUD helpers + TTL cleanup | `app/services/queue.py::recover_stuck_jobs` (короткий хелпер с `AsyncSessionLocal`) + `WarmupWorker` (для cleanup-task периодик) | role-match |
| `tests/conftest.py` (расширение) | test infrastructure | fixture factory | `tests/conftest.py` (Phase 1) — расширяем, не создаём | exact |
| `tests/test_migration_013.py` | test | schema smoke | `tests/test_migration_012.py` | exact (тот же раннер миграции через `exec_driver_sql`) |
| `tests/test_folders.py` | test | integration | `tests/test_workspace_router.py` | exact |
| `tests/test_contacts.py` | test | integration + multipart | `tests/test_workspace_router.py` + новый pattern multipart upload | role-match |
| `tests/test_onboarding.py` | test | integration с mock'нутым Telethon | `tests/test_workspace_router.py` + monkeypatch pattern | role-match |
| `tests/test_senders.py` | test | integration | `tests/test_workspace_router.py` | exact |
| `tests/test_contact_check_worker.py` | test | unit (worker.tick) | `tests/test_workspace_router.py` (как async-test) + новый pattern (моки CheckerService) | role-match |
| `tests/test_listener_reconcile.py` | test | unit (reconcile diff) | (нет аналога) — new pattern с моками `make_telegram_client` | new pattern |
| `tests/test_phone_normalization.py` | test | unit (pure function) | (нет аналога) — простой `assert normalize_to_e164(...) == ...` без фикстур | new pattern |

### Переписываются полностью (новая реализация)

| Файл | Роль | Data Flow | Ближайший аналог | Match Quality |
|------|------|-----------|------------------|---------------|
| `app/routers/onboarding.py` | router | request-response + Telethon stateful | `app/routers/workspace.py` (AuthDep pattern) + текущий `app/routers/onboarding.py` (Telethon-вызовы, **БЕЗ** `_onboarding_sessions: dict`) | смешанный: AuthDep из workspace.py, Telethon-flow из старого onboarding.py |
| `app/routers/senders.py` | router | request-response CRUD | `app/routers/workspace.py` (workspace-scoped CRUD) + текущий senders.py (CRUD-структура **БЕЗ** `subprocess.run` и `is_active`) | смешанный |
| `app/routers/check_contacts.py` | router | request-response (ad-hoc) | `app/routers/workspace.py` (AuthDep) + текущий check_contacts.py (вызов `checker_service`) | смешанный |

### Модифицируются точечно

| Файл | Что меняется | Аналог изменения |
|------|--------------|------------------|
| `app/models/__init__.py` | +`Folder`, +`Contact`, +`OnboardingSession`, +`CsvImport`; расширение `Sender` (`lifecycle_status`, `rate_per_*`); удаление `Sender.is_active` | Существующая структура моделей в том же файле — копируем стиль `Workspace` / `WorkspaceApiKey` (Phase 1, lines 31-68) |
| `app/schemas/__init__.py` | +новые Pydantic v2 модели | Текущий стиль `SenderCreate` / `SenderUpdate` / `SenderResponse` (lines 71-109) + `ProxyConfig` (lines 8-13) |
| `app/main.py` | +5 include_router, +2 worker.start/stop в lifespan | Существующий lifespan (lines 23-43) — добавляем по образцу `queue_worker.start()` |
| `app/services/queue.py` | выпил `MAX_MSGS_PER_*` constants, чтение из `sender.rate_per_*` | Сам queue.py — паттерн worker.tick остаётся; меняется только `_check_rate_limits` |
| `app/services/listener.py` | +`_reconcile_loop` task; фильтр `is_active` → `lifecycle_status='active' AND auth_status='ok'`; выпил `is_active = false` из `_set_auth_status` | `WarmupWorker._run` (паттерн периодичного task'а) + текущий `get_active_senders` (только меняем WHERE) |
| `app/services/warmup.py` | фильтр `s.is_active` → `s.lifecycle_status='active' AND s.auth_status='ok'` | line 171 — точечный SQL-фикс |
| `app/services/rotation.py` | те же `is_active` → `lifecycle_status` правки (lines 48, 147) | точечный SQL-фикс |
| `app/routers/health.py` | line 37: `s.is_active` → derived check | точечный фикс |
| `docker-compose.yml` | удалить `docker.sock` volume mount (если есть) на сервисе `api` | конфиг — простое удаление строки |

---

## Pattern Assignments (детальные code excerpts)

### 1. `migrations/013_phase2.sql` (migration, DDL)

**Analog:** `migrations/012_workspace.sql`

**Имя и header pattern** (012_workspace.sql:1-7):
```sql
-- migrations/013_phase2.sql
-- Phase 2: TG Accounts & Contacts foundation
-- Adds: contacts, folders, onboarding_sessions, csv_imports tables
-- Modifies: senders (+ rate_per_*, lifecycle_status; - is_active)
-- БД должна быть пустой (Phase 1 D-01). Все операторы идемпотентны (IF NOT EXISTS / IF EXISTS).

BEGIN;
```

**CREATE TABLE pattern (workspace-scoped tenant table)** (012_workspace.sql:18-32):
```sql
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
```

**Partial unique index pattern** (012_workspace.sql:46-49):
```sql
CREATE INDEX IF NOT EXISTS idx_workspace_api_keys_prefix_active
    ON workspace_api_keys(prefix)
    WHERE revoked_at IS NULL;
```
→ Для D-02 `contacts (workspace_id, phone) WHERE phone IS NOT NULL` копируется этот же шаблон.

**ALTER TABLE pattern (расширение существующей таблицы)** (012_workspace.sql:57-62):
```sql
ALTER TABLE senders
    ADD COLUMN IF NOT EXISTS workspace_id UUID NOT NULL
        REFERENCES workspaces(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_senders_workspace
    ON senders(workspace_id);
```

**Финал миграции** (012_workspace.sql:134):
```sql
COMMIT;
```

**Скопировать verbatim:**
- BEGIN/COMMIT обёртка
- Header-комментарий с описанием изменений и упоминанием Phase 1 D-01 (пустая БД)
- `IF NOT EXISTS` / `IF EXISTS` на каждом операторе
- `gen_random_uuid()`, `TIMESTAMPTZ NOT NULL DEFAULT NOW()`, `ON DELETE CASCADE`
- `CONSTRAINT <table>_<field>_check CHECK (...)` — Phase 1 паттерн для enum-like полей (тот же что в `user_workspaces_role_check`)

**Адаптировать:**
- Новые таблицы `folders`, `contacts`, `onboarding_sessions`, `csv_imports` — поля по D-01/D-05/D-16
- `DROP CONSTRAINT IF EXISTS` → `ADD CONSTRAINT` для `senders_role_check` (D-21 + CONTEXT `<specifics>`)
- `ALTER TABLE senders DROP COLUMN IF EXISTS is_active` — финал миграции (D-11)
- Partial unique index `(workspace_id, phone) WHERE phone IS NOT NULL` (D-02)

**Anti-patterns (НЕ повторять):**
- Не использовать `CREATE TYPE ... AS ENUM` — `String + CHECK` (RESEARCH §SQLEnum vs String+CHECK; Phase 1 precedent `user_workspaces_role_check`)
- Не делать backfill старых данных — БД пустая (D-01 Phase 1)
- Не делать миграцию неидемпотентной (CLAUDE.md: всегда `IF NOT EXISTS`)

---

### 2. `app/routers/folders.py` (router, CRUD)

**Analog:** `app/routers/workspace.py` (свежий Phase 1 паттерн workspace-scoped CRUD на AuthDep)

**Header / imports pattern** (workspace.py:1-33):
```python
"""
Workspace router (Phase 1 — TENT-03, AUTH-01 UX, AUTH-04 stateless).

Endpoints:
  POST   /api/v1/auth/me                       — bootstrap (JWT only)
  GET    /api/v1/workspace                     — JWT or API key
  ...
"""

import logging
from datetime import datetime
from typing import List, Optional
from uuid import UUID

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
```

**AuthCtx + workspace isolation pattern** (workspace.py:139-161):
```python
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
    ...
```

**Cross-tenant guard pattern** (workspace.py:292-306):
```python
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
```

**HTTPException structured-error pattern** (workspace.py:152-154, CONVENTIONS.md):
```python
raise HTTPException(
    status_code=404,
    detail={"code": "WORKSPACE_NOT_FOUND", "message": "Workspace not found"},
)
```

**logger.info шаблон** (workspace.py:194, 237-240):
```python
logger.info(f"[workspace] renamed id={workspace.id} to '{workspace.name}'")
logger.info(
    f"[api_key] created workspace={ctx.workspace_id} "
    f"prefix={prefix} name='{request.name}' id={api_key.id}"
)
```

**Скопировать verbatim:**
- Импорт-блок (`from app.utils.auth import AuthCtx, auth_dep`, `from app.database import get_db`)
- Сигнатура endpoint'ов: `ctx: AuthCtx = Depends(auth_dep), db: AsyncSession = Depends(get_db)`
- WHERE-фильтр `.where(Folder.workspace_id == ctx.workspace_id)` + комментарий `# TODO(v2-rls): replaced by RLS policy`
- HTTPException с dict `{code, message}`
- `logger.info(f"[folders] ...")` префикс

**Адаптировать:**
- `prefix="/api/v1/folders"`, `tags=["folders"]`
- D-06 — 409 Conflict с body `{contact_count, active_campaigns: []}` и опциональный `?force=true`:
  ```python
  if not force and contact_count > 0:
      raise HTTPException(
          status_code=409,
          detail={
              "code": "FOLDER_NOT_EMPTY",
              "contact_count": contact_count,
              "active_campaigns": [],  # TODO(phase-4): also block on active campaign attachment
          },
      )
  ```
- D-09 helper `_get_or_create_folder_by_name(db, ctx, name)` — раздельный helper, используемый и в contacts.py для auto-create

**Anti-patterns (НЕ повторять):**
- Не `_: str = Depends(verify_api_key)` (broken, удалён в Phase 1)
- Не SELECT без `.where(... == ctx.workspace_id)`
- Не `from app.routers.auth import verify_api_key` (модуль не существует)

---

### 3. `app/routers/contacts.py` (router, CRUD + multipart upload)

**Analog:** `app/routers/workspace.py` (AuthDep) + `app/routers/check_contacts.py` (Pydantic schemas + batch handling)

**Pydantic v2 request schema с валидатором** (check_contacts.py:29-48):
```python
class CheckContactsRequest(BaseModel):
    phones: List[str] = Field(
        ...,
        min_length=1,
        max_length=20,
        description="Список номеров для проверки (формат: +7XXXXXXXXXX). Максимум 20 за раз.",
    )
    checker_slug: str = Field(..., description="Slug checker-аккаунта (role='checker')")

    @field_validator("phones")
    @classmethod
    def validate_phone_format(cls, v: List[str]) -> List[str]:
        for phone in v:
            if not PHONE_RE.match(phone):
                raise ValueError(
                    f"Invalid phone format: '{phone}'. Expected international format, e.g. +79001234567"
                )
        return v
```
→ Для contacts: использовать `app.utils.phone.normalize_to_e164` в `model_validator(mode='after')` вместо просто regex-check.

**Pydantic v2 response schema с `from_attributes=True`** (schemas/__init__.py:91-105):
```python
class SenderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    slug: str
    name: str
    phone: str
    is_active: bool
    ...
```

**Структурированный return шаблон** (check_contacts.py:119-125):
```python
return CheckContactsResponse(
    checked=summary["checked"],
    registered=summary["registered"],
    not_registered=summary["not_registered"],
    flood_wait_hit=summary["flood_wait_hit"],
    results=[PhoneCheckResult(**r) for r in summary["results"]],
)
```

**Multipart upload pattern (NEW в проекте — FastAPI UploadFile):**
```python
from fastapi import UploadFile, File

@router.post("/import/preview")
async def import_preview(
    file: UploadFile = File(...),
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    if file.size and file.size > 5 * 1024 * 1024:  # 5MB hard limit (RESEARCH §CSV Import Pitfalls)
        raise HTTPException(413, {"code": "FILE_TOO_LARGE"})
    raw = await file.read()
    preview = csv_import.parse_preview(raw)  # delegates to service
    # Save to csv_imports (BYTEA) — RESEARCH §CSV Import Storage Option B
    import_row = CsvImport(
        workspace_id=ctx.workspace_id,
        file_data=raw,
        columns=preview["columns"],
        suggested_mapping=preview["suggested_mapping"],
        encoding=preview["encoding"],
        delimiter=preview["delimiter"],
    )
    db.add(import_row)
    await db.commit()
    return {"import_id": str(import_row.id), **preview}
```

**Auto-create folder ON CONFLICT pattern (RESEARCH Pitfall 4):**
```python
from sqlalchemy import text

async def _get_or_create_folder(db, workspace_id, name) -> UUID:
    row = await db.execute(
        text("""
            INSERT INTO folders (workspace_id, name)
            VALUES (:wid, :name)
            ON CONFLICT (workspace_id, name) DO UPDATE SET updated_at = NOW()
            RETURNING id
        """),
        {"wid": str(workspace_id), "name": name},
    )
    return row.scalar()
```

**Скопировать verbatim:**
- Pydantic schema стиль из `schemas/__init__.py` (PascalCase + Request/Response/Create/Update суффиксы; `model_config = ConfigDict(from_attributes=True)` для response)
- `Depends(auth_dep)` (тот же endpoint работает и для JWT, и для X-Workspace-Key — D-10 push API)
- HTTPException-dict шаблон

**Адаптировать:**
- Endpoint set: `GET /contacts`, `POST /contacts` (push), `POST /contacts/import/preview`, `POST /contacts/import`, `POST /contacts/recheck`, `POST /contacts/{id}/move`, `DELETE /contacts/{id}` (RESEARCH §Lovable / Frontend API Contract)
- `ctx.source` ("jwt" | "api_key") — НЕ ассумить `user_id is not None` (RESEARCH Pitfall 8)
- 202 Accepted при `/import` (D-19: контакты вставляются с `tg_status='pending'`, фоновый worker подхватит)

**Anti-patterns (НЕ повторять):**
- Не `Sender.is_active.is_(True)` (см. check_contacts.py:90) — после Phase 2 это `Sender.auth_status == 'ok'` (для checker'а)
- Не игнорировать `ctx.workspace_id` в WHERE
- Не использовать `from app.routers.auth import verify_api_key`

---

### 4. `app/routers/senders.py` (router, REWRITE)

**Analog:** `app/routers/workspace.py` (AuthDep + workspace-isolation) + текущий `app/routers/senders.py` (CRUD-структура **БЕЗ** subprocess и is_active)

**Что копировать из текущего senders.py (паттерн CRUD):**
- selectinload-паттерн для подгрузки relationship (senders.py:59-64):
  ```python
  from sqlalchemy.orm import selectinload
  result = await db.execute(
      select(Sender).options(selectinload(Sender.ai_context)).order_by(Sender.name)
  )
  ```
- Cascade delete контактов/conversations при `DELETE /senders/{id}` (senders.py:237-273)
- SpamBot-check endpoint (senders.py:276-324) — оставляем; **но `sender.is_active = False` (line 308) убираем** — D-11 derived 'error' через auth_status

**Derived status helper (NEW pattern для Phase 2 D-11):**
```python
def _derive_status(sender: Sender) -> str:
    """D-11 derived status: error > lifecycle_status."""
    if sender.auth_status != "ok":
        return "error"
    return sender.lifecycle_status  # 'active' | 'warmup' | 'paused'


def sender_to_response(sender: Sender) -> dict:
    return {
        "id": sender.id,
        "slug": sender.slug,
        "name": sender.name,
        "phone": sender.phone,
        "status": _derive_status(sender),           # derived
        "auth_status": sender.auth_status,           # raw, для tooltip
        "lifecycle_status": sender.lifecycle_status, # raw, для UI controls
        "rate_limits": {
            "per_minute": sender.rate_per_min,
            "per_hour": sender.rate_per_hour,
            "per_day": sender.rate_per_day,
        },
        "role": sender.role,
        "proxy": sender.proxy,
        "ai_context_id": sender.ai_context_id,
        "ai_context_name": sender.ai_context.name if sender.ai_context else None,
        "last_used_at": sender.last_used_at,
        "created_at": sender.created_at,
    }
```

**Rate-limit warnings shape (NEW для D-14):**
```python
# Hard cap (D-14): 10 / 50 / 300 → 422
RATE_HARD_CAP = {"rate_per_min": 10, "rate_per_hour": 50, "rate_per_day": 300}
# Soft cap (green corridor): 4 / 20 / 150 → 200 + warnings[]
RATE_SOFT_CAP = {"rate_per_min": 4, "rate_per_hour": 20, "rate_per_day": 150}

def _validate_rate_limits(payload: SenderUpdate) -> list[dict]:
    warnings = []
    for field, hard in RATE_HARD_CAP.items():
        val = getattr(payload, field, None)
        if val is None:
            continue
        if val > hard:
            raise HTTPException(422, {
                "code": "RATE_LIMIT_EXCEEDS_HARD_CAP",
                "field": field,
                "value": val,
                "hard_cap": hard,
                "message": "exceeds maximum safe limit, contact support if you need higher",
            })
        if val > RATE_SOFT_CAP[field]:
            warnings.append({
                "field": field,
                "value": val,
                "recommended_max": RATE_SOFT_CAP[field],
                "severity": "warning",
            })
    return warnings
```

**Скопировать verbatim:**
- Selectinload-паттерн для `Sender.ai_context`
- Структура CRUD: list / get / create / patch / delete (без `subprocess.run`)
- SpamBot endpoint (без `is_active = False`)

**Адаптировать:**
- Все endpoint signature → `ctx: AuthCtx = Depends(auth_dep)`
- Все SELECT → `.where(Sender.workspace_id == ctx.workspace_id)`
- `is_active` → `lifecycle_status` (RESEARCH §Hidden Dependencies — 5 мест в этом файле)
- `POST /senders/{id}/assign-proxy` (D-22) — новый endpoint
- Response через `sender_to_response` с derived `status` + `rate_limits` nested object (CONTEXT `<specifics>`)

**Anti-patterns (выпиливаем все три):**
- `_restart_listener` функция (lines 36-50) — целиком удалить
- `subprocess.run(["docker", "restart", "telegram-listener"], ...)` — все 3 места (lines 43, 148, 221)
- `import subprocess` — удалить
- `sender.is_active = False` (line 308 SpamBot) — заменить на просто `sender.auth_status = new_auth_status`
- `from app.routers.auth import verify_api_key` — заменить на `from app.utils.auth import auth_dep, AuthCtx`
- `if existing.scalar_one_or_none()` без `.where(workspace_id)` (line 75-78) — добавить workspace-фильтр

---

### 5. `app/routers/onboarding.py` (router, REWRITE с Telethon stateful)

**Analog:** `app/routers/workspace.py` (AuthDep) + текущий `app/routers/onboarding.py` (Telethon-вызовы и Pydantic schemas)

**Что копировать из текущего onboarding.py (Telethon-flow ОК, но wrapping меняется):**

Структура Telethon-вызовов (старый onboarding.py + RESEARCH §"Telethon Onboarding Flow"):
```python
# /start
client = make_telegram_client(StringSession(), proxy=proxy_dict)
await client.connect()
sent_code = await client.send_code_request(phone)
# sent_code.phone_code_hash — нужен для verify-code

# /verify-code (hot path)
await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
# Может бросить SessionPasswordNeededError → 2FA flow

# /verify-2fa
await client.sign_in(password=password)

# /qr-start
qr_login = await client.qr_login()
qr_image_bytes = _make_qr_image(qr_login.url)
```

**Pydantic schemas (старый onboarding.py:49-100):**
```python
class StartOnboardingRequest(BaseModel):
    phone: str

class VerifyCodeRequest(BaseModel):
    session_id: str
    code: str
    role: Optional[str] = Field("sender", description="'sender' = отправщик, 'checker' = проверщик")

class Verify2FARequest(BaseModel):
    session_id: str
    password: str
```
→ Скопировать verbatim (имена + поля), добавить `OnboardingStatusResponse` для derived статусов.

**NEW pattern: persistent state recovery (D-17, RESEARCH §"Persistent state recovery"):**
```python
# In-process dict для TelegramClient объектов (D-17): Telethon client не сериализуется.
# Recovery from DB происходит при cache miss.
_in_process_clients: dict[str, "TelegramClient"] = {}


@router.post("/verify-code")
async def verify_code(
    request: VerifyCodeRequest,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    # 1. Load state (workspace-isolated)
    result = await db.execute(
        select(OnboardingSession).where(
            OnboardingSession.id == request.session_id,
            OnboardingSession.workspace_id == ctx.workspace_id,  # TODO(v2-rls)
            OnboardingSession.status == "code_sent",
        )
    )
    session_row = result.scalar_one_or_none()
    if not session_row or session_row.expires_at < datetime.now(timezone.utc):
        raise HTTPException(404, {"code": "SESSION_NOT_FOUND"})

    # 2. Resolve client — HOT or RECOVERY
    sid_str = str(session_row.id)
    client = _in_process_clients.get(sid_str)
    if client is None:
        # RECOVERY: восстанавливаем из БД
        logger.info(f"📱 Recovering client from DB: session={sid_str[:8]}...")
        client = make_telegram_client(
            StringSession(decrypt_session(session_row.encrypted_session_string)),
            proxy=session_row.proxy,
        )
        await client.connect()
        _in_process_clients[sid_str] = client

    # 3. sign_in ... (см. RESEARCH §Telethon onboarding flow)
```

**Скопировать verbatim:**
- Telethon error mapping (PhoneCodeInvalidError → 400 / PhoneCodeExpiredError → 400 / SessionPasswordNeededError → 200 "2fa_required" / FloodWaitError → 429 retry_after)
- `make_telegram_client` импорт из `app.services.telegram`
- `encrypt_session` / `decrypt_session` — единственный способ работы с session_string'ами
- `import qrcode; from io import BytesIO; import base64` для QR
- Lazy proxy lookup из ProxyPool (паттерн похож на senders.py:107-145, но через workspace-фильтр)

**Адаптировать:**
- `_onboarding_sessions: dict[str, dict] = {}` → DROP полностью; вместо этого:
  - Persistent state в `onboarding_sessions` table (D-16)
  - In-process dict ТОЛЬКО для `TelegramClient` объектов с key=`onboarding_session.id` (D-17)
- Все 12 endpoint'ов: `_: str = Depends(verify_api_key)` → `ctx: AuthCtx = Depends(auth_dep)`
- Все SELECT/INSERT — `workspace_id == ctx.workspace_id`
- `_auto_save_reauth` (lines 200+) — выпиливает `sender.is_active = True` (заменено: после успешного re-auth `sender.auth_status = 'ok'`)
- `subprocess.run(['docker','restart','telegram-listener'])` (lines 209-215) — DROP (D-18, заменяется listener reconcile-loop'ом)

**Anti-patterns (выпиливаем):**
- `_onboarding_sessions: dict[str, dict] = {}` (line 46) — DROP
- `subprocess.run(["docker", "restart", ...])` (lines 209-215) — DROP
- `from app.routers.auth import verify_api_key` (line 34) — DROP
- `sender.is_active = True` (line 204) — заменить на `auth_status='ok'`

---

### 6. `app/services/contact_check_worker.py` (background worker)

**Analog:** `app/services/queue.py::QueueWorker` (background asyncio task) + `app/services/warmup.py::WarmupWorker` (TICK_INTERVAL)

**Worker class skeleton** (queue.py:67-107):
```python
class QueueWorker:
    """Background asyncio task that drains the message_queue table."""

    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._idle_event: Optional[asyncio.Event] = None

    def start(self):
        if self._task is None or self._task.done():
            self._running = True
            self._idle_event = asyncio.Event()
            self._idle_event.set()
            self._task = asyncio.create_task(self._run(), name="queue-worker")
            logger.info("Queue worker started")

    async def stop(self):
        self._running = False
        if self._idle_event:
            try:
                await asyncio.wait_for(self._idle_event.wait(), timeout=60.0)
            except asyncio.TimeoutError:
                logger.warning("Graceful shutdown: timeout after 60s, forcing stop")
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Queue worker stopped")

    async def _run(self):
        while self._running:
            try:
                await self._tick()
            except Exception as exc:
                logger.error(f"Queue worker error: {exc}", exc_info=True)
            await asyncio.sleep(3)   # poll interval
```

**Alternative `_run` pattern с CancelledError** (warmup.py:120-129):
```python
async def _run(self):
    """Главный цикл воркера."""
    while self._running:
        try:
            await self._tick()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"❌ Ошибка в warmup tick: {e}", exc_info=True)
        await asyncio.sleep(self.TICK_INTERVAL)
```
→ Использовать вариант `warmup.py` (он чище — explicit CancelledError handling).

**Module-level singleton** (queue.py + warmup.py финал файла):
```python
# В самом конце файла
contact_check_worker = ContactCheckWorker()
```

**Реальный _tick body (RESEARCH §"ContactCheckWorker tick"):**
```python
async def _tick(self):
    async with AsyncSessionLocal() as db:
        result = await db.execute(text("""
            SELECT c.id, c.workspace_id, c.phone, c.username,
                   s.id AS checker_id, s.slug AS checker_slug,
                   s.session_string, s.proxy,
                   s.rate_per_min, s.rate_per_hour, s.rate_per_day
            FROM contacts c
            JOIN LATERAL (
                SELECT id, slug, session_string, proxy,
                       rate_per_min, rate_per_hour, rate_per_day
                FROM senders
                WHERE workspace_id = c.workspace_id
                  AND role = 'checker'
                  AND auth_status = 'ok'
                LIMIT 1
            ) s ON true
            WHERE c.tg_status = 'pending'
              AND c.phone IS NOT NULL
            ORDER BY c.created_at ASC
            LIMIT :n
        """), {"n": self.batch_size})

        rows = result.fetchall()
        if not rows:
            return

    # Reuse existing CheckerService — он уже умеет FloodWait + polite delay + lock per checker_slug
    from itertools import groupby
    for checker_id, items in groupby(rows, key=lambda r: r.checker_id):
        items = list(items)
        phones = [r.phone for r in items]
        first = items[0]

        try:
            summary = await checker_service.check_phones(
                checker_id=str(checker_id),
                checker_slug=first.checker_slug,
                encrypted_session=first.session_string,
                phones=phones,
                proxy=first.proxy,
            )
        except Exception as exc:
            logger.error(f"ContactCheckWorker: checker {first.checker_slug} failed: {exc}", exc_info=True)
            continue

        async with AsyncSessionLocal() as db:
            for item, res in zip(items, summary["results"]):
                tg_status = "registered" if res["is_registered"] else "not_registered"
                await db.execute(text("""
                    UPDATE contacts
                    SET tg_status = :status,
                        tg_telegram_id = :tg_id,
                        tg_checked_at = NOW(),
                        updated_at = NOW()
                    WHERE id = :cid
                """), {"status": tg_status, "tg_id": res.get("telegram_id"), "cid": item.id})
            await db.commit()

        logger.info(
            f"📋 ContactCheckWorker: checker={first.checker_slug} "
            f"checked={summary['checked']} reg={summary['registered']} "
            f"not_reg={summary['not_registered']} flood={summary['flood_wait_hit']}"
        )
```

**Скопировать verbatim:**
- `start()/stop()` структура (warmup.py:101-116)
- `_run()` с CancelledError handling (warmup.py:120-129)
- Module-level `contact_check_worker = ContactCheckWorker()`
- `AsyncSessionLocal()` async-context (везде в queue.py / warmup.py)
- `checker_service.check_phones(...)` — переиспользование, не дублирование (RESEARCH §"Reuse existing CheckerService")

**Адаптировать:**
- `TICK_INTERVAL = 5` (RESEARCH §"ContactCheckWorker — batch size"), `batch_size = 5`
- Эмодзи `📋` в logger.info (CONVENTIONS.md: emoji prefixes используются в `listener.py`/`warmup.py`)
- env vars override через `os.environ.get("CONTACT_CHECK_BATCH_SIZE", "5")` (опционально C-06)

**Anti-patterns (НЕ повторять):**
- Не делать `time.sleep()` — только `asyncio.sleep` (CLAUDE.md)
- Не печатать `print()` (CLAUDE.md)
- Не дублировать FloodWait-handling — он уже в `CheckerService.check_phones` (lines 211-219)
- Не игнорировать workspace boundaries — checker берётся только в своём workspace (JOIN LATERAL по `s.workspace_id = c.workspace_id`)

---

### 7. `app/services/csv_import.py` (transform service)

**Analog:** `app/services/encryption.py` (модуль чистых хелперов; нет аналога CSV в проекте)

**Стиль pure-functions модуля** (encryption.py header + signature):
```python
"""CSV-импорт: parse_preview, suggest_mapping, apply_import."""

import csv
import io
import logging
import re
from typing import BinaryIO

logger = logging.getLogger(__name__)
```

**Концевые функции skeleton (RESEARCH §"CSV Import Pitfalls" + §"Skeleton"):**
```python
ENCODING_FALLBACKS = ["utf-8-sig", "cp1251"]


def parse_preview(file_bytes: bytes, max_rows: int = 50) -> dict:
    """Returns {columns, sample_rows, delimiter, encoding, looks_like_no_header}."""
    text = None
    used_encoding = None
    for enc in ENCODING_FALLBACKS:
        try:
            text = file_bytes.decode(enc)
            used_encoding = enc
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError("INVALID_ENCODING")

    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","

    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = list(reader)
    if not rows:
        raise ValueError("EMPTY_FILE")

    headers = [h.strip() for h in rows[0]]
    sample_rows = [dict(zip(headers, [c.strip() for c in r])) for r in rows[1:max_rows+1]]

    return {
        "columns": headers,
        "sample_rows": sample_rows,
        "delimiter": delimiter,
        "encoding": used_encoding,
        "looks_like_no_header": _heuristic_no_header(headers),
    }


def suggest_mapping(columns: list[str]) -> dict[str, str]:
    """Heuristic: column name → contact field. Returns {col_idx: field_name}."""
    aliases = {
        "phone": ["phone", "телефон", "tel", "mobile", "номер"],
        "username": ["username", "юзернейм", "tg", "telegram"],
        "full_name": ["name", "имя", "fio", "фио", "full_name", "fullname"],
        "source": ["source", "источник", "src", "origin"],
    }
    result = {}
    for idx, col in enumerate(columns):
        col_norm = col.lower().strip()
        for field, options in aliases.items():
            if col_norm in options:
                result[str(idx)] = field
                break
    return result
```

**Анти-паттерны (НЕ повторять):**
- Не использовать `pandas` (RESEARCH §"Don't Hand-Roll" — overkill)
- Не использовать `phonenumbers` (RESEARCH § Phone Normalization — overkill)
- Не открывать файл с `encoding='utf-8'` напрямую — Excel BOM сломает (использовать `utf-8-sig` + fallback `cp1251`)

---

### 8. `app/utils/phone.py` (pure utility)

**Analog:** `app/services/encryption.py` (структура pure-helper модуля); нет существующего аналога

**Code (RESEARCH §Phone Normalization E.164):**
```python
"""Phone normalization to E.164 format. RU-centric heuristics + ITU spec."""

import re

_NON_DIGIT = re.compile(r"\D+")


def normalize_to_e164(raw: str) -> str | None:
    """Normalize phone to E.164 format (+XXXXXXXXX). Returns None if invalid.

    Rules:
    - Strip all non-digit (preserves leading + if present, removes everything else)
    - RU heuristic: 11 digits starting with 8 → replace 8 with 7
    - Add leading + if missing
    - Validate: + followed by 7..15 digits (ITU E.164 spec)
    """
    if not raw:
        return None
    had_plus = raw.lstrip().startswith("+")
    digits = _NON_DIGIT.sub("", raw)
    if not digits:
        return None
    if not had_plus and len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    e164 = "+" + digits
    if not re.match(r"^\+\d{7,15}$", e164):
        return None
    return e164
```

**Anti-patterns (НЕ повторять):**
- Не использовать `phonenumbers` (RESEARCH §Don't Hand-Roll)
- Не делать наивный `phone.strip().replace(" ", "").replace("-", "")` (текущий onboarding.py:246-248) — он не покрывает leading-8 случай

---

### 9. `app/models/__init__.py` (расширение)

**Analog:** Сами модели `Workspace`/`WorkspaceApiKey`/`Sender` в этом же файле (lines 31-95)

**Tenant-table pattern** (models/__init__.py:73-95):
```python
class Sender(Base):
    __tablename__ = "senders"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True),
                          ForeignKey("workspaces.id", ondelete="CASCADE"),
                          nullable=False)
    slug = Column(String(50), unique=True, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    ...
    created_at = Column(DateTime(timezone=True), server_default=func.now())
```

**Relationships pattern** (lines 92-95):
```python
    # Relationships
    messages = relationship("MessageLog", back_populates="sender")
    contacts = relationship("ContactCache", back_populates="sender")
    ai_context = relationship("AIContext", back_populates="senders")
```

**Section divider style** (models/__init__.py:29, 71, 239, 304):
```python
# ─── Multi-tenant foundation (Phase 1 — TENT-01..04) ─────────────────────────
# ─── Tenant-scoped models (workspace_id added per Phase 1) ───────────────────
# ─── Warmup ───────────────────────────────────────────────────────────────────
```

**Что добавить (новые модели для Phase 2):**

```python
# ─── Phase 2: Folders & Contacts ─────────────────────────────────────────────

class Folder(Base):
    __tablename__ = "folders"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True),
                          ForeignKey("workspaces.id", ondelete="CASCADE"),
                          nullable=False)
    name = Column(String(100), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Contact(Base):
    __tablename__ = "contacts"
    # NB: НЕ ПУТАТЬ с ContactCache (per-sender resolve cache из Phase 0)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True),
                          ForeignKey("workspaces.id", ondelete="CASCADE"),
                          nullable=False)
    folder_id = Column(UUID(as_uuid=True),
                       ForeignKey("folders.id", ondelete="CASCADE"),
                       nullable=False)
    phone = Column(String(20), nullable=True)
    username = Column(String(50), nullable=True)
    full_name = Column(String(200), nullable=True)
    source = Column(String(100), nullable=True)
    custom = Column(JSONB, nullable=False, server_default='{}')
    # RESEARCH §SQLEnum vs String+CHECK: используем String(20) + CHECK в миграции
    tg_status = Column(String(20), nullable=False, server_default='pending')
    tg_telegram_id = Column(BigInteger, nullable=True)
    tg_username_resolved = Column(String(50), nullable=True)
    tg_error = Column(Text, nullable=True)
    tg_checked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    folder = relationship("Folder")


class OnboardingSession(Base):
    __tablename__ = "onboarding_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True),
                          ForeignKey("workspaces.id", ondelete="CASCADE"),
                          nullable=False)
    phone = Column(String(20), nullable=False)
    phone_code_hash = Column(Text, nullable=False)
    encrypted_session_string = Column(Text, nullable=False)
    role = Column(String(20), nullable=False, server_default='sender')
    proxy = Column(JSONB, nullable=True)
    status = Column(String(20), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class CsvImport(Base):
    __tablename__ = "csv_imports"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True),
                          ForeignKey("workspaces.id", ondelete="CASCADE"),
                          nullable=False)
    file_data = Column(LargeBinary, nullable=False)  # SQLAlchemy → BYTEA
    columns = Column(JSONB, nullable=False)
    suggested_mapping = Column(JSONB, nullable=False)
    encoding = Column(String(20), nullable=True)
    delimiter = Column(String(5), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)
```

**Расширение `Sender` модели:**
```python
class Sender(Base):
    # ... (всё что есть)
    # NEW Phase 2:
    lifecycle_status = Column(String(20), nullable=False, server_default='active')
    rate_per_min = Column(Integer, nullable=False, server_default='4')
    rate_per_hour = Column(Integer, nullable=False, server_default='20')
    rate_per_day = Column(Integer, nullable=False, server_default='150')
    # REMOVED:
    # is_active = Column(Boolean, default=True, server_default='true')   # ← DROP (D-11)
```

**Скопировать verbatim:**
- UUID PK с `default=uuid.uuid4`
- `ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False`
- `server_default=func.now()` для timestamps
- Section divider `# ─── ... ─` style
- `JSONB` для custom-полей (как у `AIContext.faq`)

**Адаптировать:**
- Импорт `from sqlalchemy import LargeBinary` (для `csv_imports.file_data`)
- НЕ использовать `SQLEnum(...)` для новых статусов (Phase 1 precedent + RESEARCH §SQLEnum vs String+CHECK)

---

### 10. `app/schemas/__init__.py` (расширение)

**Analog:** Существующий `app/schemas/__init__.py` (Pydantic v2 + ConfigDict)

**Response schema pattern** (schemas/__init__.py:91-105):
```python
class SenderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    slug: str
    name: str
    phone: str
    ...
```

**Request schema pattern** (schemas/__init__.py:71-78):
```python
class SenderCreate(BaseModel):
    slug: str = Field(..., min_length=2, max_length=50)
    name: str = Field(..., max_length=100)
    phone: str = Field(..., max_length=20)
    session_string: str
    ai_context_id: Optional[UUID] = Field(None, description="ID контекста AI для этого sender")
    role: Optional[str] = Field("sender", description="...")
    proxy: Optional[ProxyConfig] = Field(None, description="Прокси для подключения к Telegram")
```

**model_validator (cross-field validation)** (schemas/__init__.py:27-31):
```python
@model_validator(mode="after")
def sender_or_context_required(self) -> "SendMessageRequest":
    if not self.sender and not self.ai_context_id:
        raise ValueError("Either 'sender' or 'ai_context_id' must be provided")
    return self
```

**Literal-based enum (RESEARCH §Pattern 3):**
```python
class SenderUpdate(BaseModel):
    name: Optional[str] = None
    lifecycle_status: Optional[Literal['active', 'warmup', 'paused']] = None
    rate_per_min: Optional[int] = Field(None, ge=1, le=10)    # hard cap 10
    rate_per_hour: Optional[int] = Field(None, ge=1, le=50)   # hard cap 50
    rate_per_day: Optional[int] = Field(None, ge=1, le=300)   # hard cap 300
    proxy: Optional[ProxyConfig] = None
    ai_context_id: Optional[UUID] = None
```

**Скопировать verbatim:**
- `from pydantic import BaseModel, ConfigDict, Field, model_validator`
- Naming convention: `XxxRequest / XxxResponse / XxxCreate / XxxUpdate / XxxListResponse`
- `model_config = ConfigDict(from_attributes=True)` для response
- `Field(..., min_length=2, max_length=50)` для constrained string'ов

**Адаптировать:**
- Новые модели: `FolderResponse`, `FolderCreate`, `FolderUpdate`, `ContactResponse`, `ContactCreate`, `ContactImportPreviewResponse`, `ContactImportRequest`, `OnboardingStart`, `VerifyCodeRequest` (перекрытие со старым onboarding.py), `RecheckRequest`, `MoveContactRequest`, `AssignProxyRequest`, `WarningItem` (для D-14 warnings[]):
  ```python
  class WarningItem(BaseModel):
      field: str
      value: int
      recommended_max: int
      severity: Literal["warning"] = "warning"
  ```

**Anti-patterns (НЕ повторять):**
- Не оставлять `is_active: bool` в `SenderResponse` (текущая schemas/__init__.py:98) — заменяется на `status: Literal['active', 'warmup', 'paused', 'error']` derived
- Не оставлять `is_active` в `SenderUpdate` (line 85) — заменяется на `lifecycle_status`

---

### 11. `app/main.py` (расширение lifespan + include_router)

**Analog:** Текущий `app/main.py` (Phase 1 структура)

**Lifespan pattern** (main.py:23-43):
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    # Startup
    logger.info("Starting Outreach Platform API...")
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

**include_router pattern** (main.py:64-65):
```python
app.include_router(health.router)
app.include_router(workspace.router)
```

**Адаптировать (RESEARCH §Pattern 1):**
```python
from app.routers import health, workspace, onboarding, senders, folders, contacts, check_contacts
from app.services.contact_check_worker import contact_check_worker
from app.services.onboarding_state import onboarding_cleanup_worker

@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup
    await init_db()
    await recover_stuck_jobs()
    queue_worker.start()
    warmup_worker.start()
    contact_check_worker.start()        # NEW
    onboarding_cleanup_worker.start()   # NEW
    yield
    # shutdown
    await contact_check_worker.stop()
    await onboarding_cleanup_worker.stop()
    await queue_worker.stop()
    await warmup_worker.stop()
    await engine.dispose()

# Register new routers
app.include_router(health.router)
app.include_router(workspace.router)
app.include_router(onboarding.router)
app.include_router(senders.router)
app.include_router(folders.router)
app.include_router(contacts.router)
app.include_router(check_contacts.router)
```

**Скопировать verbatim:**
- `@asynccontextmanager` декоратор
- `await init_db()`, `await engine.dispose()`
- `logger.info(...)` после каждого старта

**Адаптировать:**
- CORS уже корректно настроен Phase 1 (`settings.cors_origins_list`) — не трогаем

---

### 12. `app/services/listener.py` (модификация — добавление reconcile loop)

**Analog для reconcile-loop:** `WarmupWorker._run` (warmup.py:120-129) + RESEARCH §"Periodic Reconcile Loop"

**Изменения в `TelegramListener.__init__` (line 129-142):**
```python
def __init__(self):
    self.clients: dict[str, TelegramClient] = {}
    self._connected_sender_ids: set[str] = set()   # NEW: для diff в reconcile
    self._proxy_snapshot: dict[str, dict | None] = {}  # NEW: detect proxy changes
    self.running = True
    # NEW Phase 2:
    self.reconcile_interval = int(os.environ.get("LISTENER_RECONCILE_INTERVAL", "30"))
    self._reconcile_task: Optional[asyncio.Task] = None
    self._stop_event = asyncio.Event()
    # ... existing buffers etc
```

**Изменения в `get_active_senders` (line 320-345):**
```python
async def get_active_senders(self) -> list[dict]:
    """Получить всех активных отправителей из БД.

    Phase 2 (D-11/D-18): фильтрация по новым полям:
      role='sender' AND lifecycle_status='active' AND auth_status='ok'
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
                SELECT id, slug, phone, session_string, ai_context_id, proxy
                FROM senders
                WHERE role = 'sender'
                  AND lifecycle_status = 'active'
                  AND auth_status = 'ok'
            """)
        )
        ...
```

**Изменения в `_set_auth_status` (line 144-155):**
```python
async def _set_auth_status(self, sender_id: str, slug: str, auth_status: str):
    """Update sender auth_status in DB. Phase 2 (D-12): is_active removed,
    derived 'error' status is computed at read-time."""
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(
                text("UPDATE senders SET auth_status = :status WHERE id = :sid"),
                {"status": auth_status, "sid": sender_id}
                # NB: убрали `is_active = false` — поля больше нет
            )
            await db.commit()
        logger.warning(f"auth_status for {slug} -> {auth_status} (derived status: error)")
    except Exception as e:
        logger.error(f"Failed to update auth_status for {slug}: {e}")
```

**Новый метод `_reconcile_loop`** (RESEARCH §"Periodic Reconcile Loop"):
```python
async def _reconcile_loop(self):
    """Periodic reconcile (D-18): diff desired senders with currently_connected,
    connect new, disconnect removed/paused, reconnect on proxy change."""
    while self.running:
        try:
            await asyncio.sleep(self.reconcile_interval)
            if not self.running:
                break

            desired_list = await self.get_active_senders()
            desired = {s["id"]: s for s in desired_list}
            current = set(self._connected_sender_ids)

            new_ids = set(desired.keys()) - current
            for sid in new_ids:
                logger.info(f"🔄 [reconcile] connecting sender={desired[sid]['slug']} workspace={desired[sid].get('workspace_id','?')[:8]}")
                asyncio.create_task(self.start_client(desired[sid]))

            removed_ids = current - set(desired.keys())
            for sid in removed_ids:
                slug = self._slug_by_id(sid)
                logger.info(f"🔄 [reconcile] disconnecting sender={slug} (lifecycle/auth status changed)")
                client = self.clients.pop(slug, None)
                if client and client.is_connected():
                    await client.disconnect()
                self._connected_sender_ids.discard(sid)
                self._proxy_snapshot.pop(sid, None)

            # Proxy-change detection (RESEARCH Pitfall 5)
            for sid, info in desired.items():
                if sid in current and self._proxy_snapshot.get(sid) != info.get("proxy"):
                    logger.warning(f"🔄 [reconcile] proxy changed for sender={info['slug']}, reconnecting")
                    slug = info["slug"]
                    client = self.clients.pop(slug, None)
                    if client and client.is_connected():
                        await client.disconnect()
                    self._connected_sender_ids.discard(sid)
                    self._proxy_snapshot.pop(sid, None)
                    # next tick подцепит как new_id

            logger.debug(f"🔄 [reconcile] tick: connected={len(self._connected_sender_ids)}, desired={len(desired)}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"❌ [reconcile] error: {e}", exc_info=True)
```

**Изменения в `run()` (line 1086-1100) и `stop()` (line 1102-1108):**
```python
async def run(self):
    logger.info("🚀 Запуск Telegram Listener с AI Engine...")
    initial = await self.get_active_senders()
    logger.info(f"📋 Найдено {len(initial)} отправителей")
    for s in initial:
        asyncio.create_task(self.start_client(s))
    # Параллельный reconcile-loop
    self._reconcile_task = asyncio.create_task(self._reconcile_loop(), name="listener-reconcile")
    # Ожидание stop-сигнала
    await self._stop_event.wait()

async def stop(self):
    logger.info("🛑 Останавливаем клиенты...")
    self.running = False
    self._stop_event.set()
    if self._reconcile_task and not self._reconcile_task.done():
        self._reconcile_task.cancel()
        try:
            await self._reconcile_task
        except asyncio.CancelledError:
            pass
    for slug, client in self.clients.items():
        await client.disconnect()
        logger.info(f"  - {slug} отключён")
```

**Скопировать verbatim:**
- Существующая структура `start_client` (auto-reconnect loop, FloodWait handling) — не трогаем
- Эмодзи-логи `🚀 🔄 ❌ ⚠️` — текущий стиль listener.py
- Signal-handler (lines 1117-1122) — оставляем, расширяем `stop()` обработкой `_reconcile_task`

**Анти-паттерны:**
- Не удалять existing `start_client` ретрай-логику — она правильная (CLAUDE.md: retry FloodWait не ломать)
- Не использовать `set` ключей `self.clients` напрямую для diff — там slugs, а не id; нужна параллельная отслеживающая структура `_connected_sender_ids`

---

### 13. `app/services/queue.py` (модификация — выпил констант)

**Analog:** Сам `queue.py` (модифицируем точечно)

**Текущий блок (queue.py:42-44) — DROP:**
```python
MAX_MSGS_PER_MINUTE = 4
MAX_MSGS_PER_HOUR = 20
MAX_MSGS_PER_DAY = 150
```

**Что добавить вместо (D-13):**
```python
# Per-sender rate limits live on senders.rate_per_min/hour/day columns now.
# _check_rate_limits reads them per sender; defaults (4/20/150) live as DB
# server_default — same empirically-tuned "green corridor".
```

**В `_check_rate_limits` (queue.py около line 200-300) — каждое сравнение со старыми константами:**
```python
# OLD: if recent_count >= MAX_MSGS_PER_MINUTE: ...
# NEW: load sender first, then use sender.rate_per_min/hour/day
sender_row = await db.execute(
    text("SELECT rate_per_min, rate_per_hour, rate_per_day FROM senders WHERE id = :sid"),
    {"sid": str(sender_id)},
)
limits = sender_row.fetchone()
if recent_minute_count >= limits.rate_per_min:
    ...
```

**Скопировать verbatim:**
- Существующая структура `_check_rate_limits` — нумерация шагов, error-handling, return-shape
- Прочие константы (MIN_SEND_INTERVAL, LONG_PAUSE_*, FLOOD_HARD_THRESHOLD, WORK_HOUR_*) — не трогаем (RESEARCH §"Other constants related to rate-limit": CLAUDE.md явный запрет; deferred Phase 4)

**Анти-паттерны:**
- Не дублировать `MAX_MSGS_PER_*` где-либо ещё после удаления
- Не менять MIN_SEND_INTERVAL/MAX_SEND_INTERVAL/LONG_PAUSE_* (CLAUDE.md)

---

### 14. Тесты — все 8 тестовых файлов

**Analog:** `tests/test_workspace_router.py` + `tests/conftest.py` (Phase 1 паттерн)

**Async-test pattern** (test_workspace_router.py:13-19):
```python
async def test_auth_me_no_auth_returns_401(async_client):
    """Без заголовков → 401."""
    response = await async_client.post("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "AUTH_REQUIRED"
```

**JWT-auth test pattern** (test_workspace_router.py:21-35):
```python
async def test_auth_me_bootstrap_creates_workspace(async_client, valid_supabase_jwt):
    token = valid_supabase_jwt(sub="me-test-user-1", email="me@example.com")
    response = await async_client.post(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert "workspace_id" in body
```

**Conftest setup pattern** (conftest.py:28-47):
```python
@pytest_asyncio.fixture(scope="session", autouse=True)
async def _setup_database():
    """Создаёт схему перед всеми тестами и применяет миграцию 012."""
    import pathlib

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
        sql = (PROJECT_ROOT / "migrations" / "012_workspace.sql").read_text()
        await conn.exec_driver_sql(sql)

    yield

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
```

**Адаптировать conftest для Phase 2 (Wave 0):**
- Расширить `_setup_database` — добавить второй `await conn.exec_driver_sql(open(...013_phase2.sql).read())`
- Добавить factory-фикстуры из RESEARCH §"pytest Fixtures": `test_workspace`, `auth_ctx`, `test_sender_factory`, `test_checker`, `test_folder`, `test_contacts_factory`, `auth_headers`, `mock_telegram_client` (RESEARCH §"Test Strategy для Telethon-tied кода")

**Скопировать verbatim:**
- `async def test_*(async_client, ...)` стиль (pytest-asyncio с auto-mode)
- `assert response.status_code == ...` + `assert response.json()["detail"]["code"] == "..."`
- `os.environ.setdefault(...)` блок в начале conftest.py для env vars

**Адаптировать (RESEARCH §"Phase Requirements → Test Map"):**
- Каждый из 16 requirement → один или несколько тестов с понятным именем (см. таблицу там)
- `mock_telegram_client` через `monkeypatch.setattr("app.routers.onboarding.make_telegram_client", _factory)` (RESEARCH §"Test Strategy")

---

## Shared Patterns (cross-cutting)

### Pattern A: Auth (применяется во ВСЕХ роутерах)

**Source:** `app/utils/auth.py::auth_dep` (Phase 1)
**Apply to:** `folders.py`, `contacts.py`, `senders.py`, `onboarding.py`, `check_contacts.py`

```python
from app.utils.auth import AuthCtx, auth_dep
from app.database import get_db

@router.get("/endpoint")
async def handler(
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    # ctx.workspace_id — всегда есть (для JWT и API key)
    # ctx.user_id — может быть None (для API key)
    # ctx.source — "jwt" | "api_key"
    ...
```

**Что обеспечивает:**
- JWT (Authorization: Bearer ...) — для UI
- X-Workspace-Key: wsk_... — для n8n / push-API (D-10)
- 401 AUTH_REQUIRED если ни одного из них нет

---

### Pattern B: Workspace Isolation (применяется в ВСЕХ запросах к tenant-таблицам)

**Source:** Phase 1 D-04, паттерн `workspace.py:115-117, 292-298`
**Apply to:** Все SELECT/UPDATE/DELETE на `folders`, `contacts`, `senders`, `onboarding_sessions`, `csv_imports`

```python
result = await db.execute(
    select(Folder).where(
        Folder.id == folder_id,
        Folder.workspace_id == ctx.workspace_id,  # cross-tenant guard
        # TODO(v2-rls): replaced by RLS policy
    )
)
folder = result.scalars().first()
if folder is None:
    # Не различаем "not found" и "not yours"
    raise HTTPException(404, {"code": "FOLDER_NOT_FOUND", "message": "..."})
```

**Что обеспечивает:**
- Cross-tenant защита от чтения/изменения чужих ресурсов
- Unified 404 ответ (security: не раскрываем существование чужих ID)

---

### Pattern C: Error Handling (HTTPException structured dict)

**Source:** CONVENTIONS.md §Error Handling + `workspace.py:152-154`
**Apply to:** Все роутеры

```python
raise HTTPException(
    status_code=404,
    detail={"code": "FOLDER_NOT_FOUND", "message": "Folder not found"},
)
```

| Код | Когда |
|-----|-------|
| 400 | Невалидный input (например, `PHONE_INVALID`) |
| 401 | `AUTH_REQUIRED`, `TOKEN_EXPIRED` |
| 403 | `JWT_REQUIRED` (для endpoint'ов, недоступных по API-key) |
| 404 | `*_NOT_FOUND` (включая cross-tenant) |
| 409 | `FOLDER_NOT_EMPTY` (D-06), `DUPLICATE_*` |
| 422 | `RATE_LIMIT_EXCEEDS_HARD_CAP` (D-14), `MAPPING_INVALID` (D-08) |
| 429 | `FLOOD_WAIT` (с `retry_after: e.seconds`) |
| 202 | `/contacts/import` (D-19) — async pipeline accepted |

---

### Pattern D: Background Worker (singleton + lifespan)

**Source:** `app/services/queue.py::QueueWorker` + `app/services/warmup.py::WarmupWorker`
**Apply to:** `contact_check_worker.py`, `onboarding_state.py::onboarding_cleanup_worker`

```python
# module-level singleton at end of file
contact_check_worker = ContactCheckWorker()

# in app/main.py lifespan
contact_check_worker.start()
...
yield
await contact_check_worker.stop()
```

---

### Pattern E: Logging Convention

**Source:** CONVENTIONS.md §Logging + listener.py / warmup.py
**Apply to:** Все services / routers

```python
import logging
logger = logging.getLogger(__name__)

logger.info(f"📱 Onboarding started: phone={phone[:6]}*** session={sid[:8]}...")
logger.info(f"[workspace] renamed id={workspace.id}")          # bracket-prefix style
logger.info(f"🔄 [reconcile] tick: ...")                       # listener-style emoji
logger.warning(f"⚠️ Не удалось ...")
logger.error(f"❌ Ошибка ...", exc_info=True)
logger.debug(f"📝 ...")
```

**Правила:**
- `exc_info=True` для `logger.error`
- API keys, session strings — НИКОГДА в логи (только `prefix` или `[:8]`)
- Phone обрезаем: `phone[:6]***`

---

### Pattern F: Pydantic v2 + ConfigDict

**Source:** `app/schemas/__init__.py:91-105`
**Apply to:** Все новые schemas

```python
from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Literal, Optional, List
from datetime import datetime
from uuid import UUID

class FolderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    contact_count: int = 0
    created_at: datetime

class SenderUpdate(BaseModel):
    name: Optional[str] = None
    lifecycle_status: Optional[Literal['active', 'warmup', 'paused']] = None
    rate_per_min: Optional[int] = Field(None, ge=1, le=10)
    rate_per_hour: Optional[int] = Field(None, ge=1, le=50)
    rate_per_day: Optional[int] = Field(None, ge=1, le=300)
```

---

### Pattern G: Encryption (session_string)

**Source:** `app/services/encryption.py` (Fernet)
**Apply to:** `onboarding_sessions.encrypted_session_string` и любая запись session_string'а в БД

```python
from app.services.encryption import encrypt_session, decrypt_session

# write
session.encrypted_session_string = encrypt_session(client.session.save())

# read
client = make_telegram_client(
    StringSession(decrypt_session(session.encrypted_session_string)),
    proxy=session.proxy,
)
```

**ОБЯЗАТЕЛЬНО** (CLAUDE.md): никакая session_string не хранится в БД нерасшифрованной.

---

## Anti-Patterns Summary (DO NOT REPEAT)

| Anti-pattern | Где живёт сейчас (file:line) | Phase 2 action |
|--------------|------------------------------|----------------|
| `_onboarding_sessions: dict[str, dict] = {}` | `app/routers/onboarding.py:46` | DROP — заменяется `onboarding_sessions` table + in-process dict только для TelegramClient |
| `subprocess.run(["docker","restart","telegram-listener"], ...)` | `app/routers/senders.py:36-50, 148, 221`; `app/routers/onboarding.py:209-215` | DROP полностью; заменяется `_reconcile_loop` в listener.py |
| `MAX_MSGS_PER_MINUTE/HOUR/DAY` глобальные константы | `app/services/queue.py:42-44` | DROP — читаем из `sender.rate_per_*` |
| `sender.is_active = False/True` writes | `app/routers/senders.py:91, 195-196, 308`; `app/services/listener.py:149`; `app/routers/onboarding.py:204` | DROP — derived 'error' через auth_status (D-12) |
| `is_active = true` фильтры в SELECT | 14 мест (RESEARCH §Hidden Dependencies) | Заменить на `lifecycle_status='active' AND auth_status='ok'` |
| `from app.routers.auth import verify_api_key` | 9 файлов (но рерайтим только onboarding/senders/check_contacts) | DROP в перерайтываемых файлах; остальные 6 остаются broken (не include_router'ятся) |
| `Sender.role = String(20)` без CHECK | `app/models/__init__.py:85` | НЕ менять Column, но добавить `CONSTRAINT senders_role_check CHECK (role IN ('sender','checker'))` в миграции 013 (CONTEXT `<specifics>`) |
| `csv` парсинг через ручной split | (нет ещё) | НЕ писать вручную — `csv.DictReader` + `csv.Sniffer` (stdlib) |
| `phonenumbers` library | (не установлен) | НЕ добавлять — RU-centric regex в `app/utils/phone.py` |
| `pandas` library | (не установлен) | НЕ добавлять — `csv` stdlib |
| `CREATE TYPE ... AS ENUM` (Postgres ENUM) | (нет в миграциях) | НЕ использовать — `String + CHECK` (Phase 1 precedent) |
| `time.sleep(...)` | (нет ничего серьёзного) | НИКОГДА — только `asyncio.sleep` (CLAUDE.md) |
| `print(...)` для отладки | (нет — все используют logger) | НИКОГДА — только `logger.info/debug/...` (CLAUDE.md) |

---

## No Analog Found

| File | Role | Reason | Fallback pattern |
|------|------|--------|------------------|
| `tests/test_listener_reconcile.py` | test (unit на reconcile diff) | До Phase 2 нет тестов на listener вообще | RESEARCH §"Test Strategy для Telethon-tied кода" + monkeypatch `make_telegram_client` |
| `app/services/csv_import.py` | pure transform (CSV → dict) | В проекте нет CSV-парсинга | stdlib `csv` + RESEARCH §"CSV Import Storage" Option B (BYTEA) |
| `app/utils/phone.py` | pure utility (string → string) | `app/utils/` пустой (только `__init__.py`) | RESEARCH §"Phone Normalization E.164" + регex-нормализатор |
| Reconcile-loop в listener.py | periodic asyncio task | Самый близкий — WarmupWorker.TICK_INTERVAL, но он в API-контейнере, не в listener-контейнере | RESEARCH §"Periodic Reconcile Loop (listener.py)" — полный skeleton там |

---

## Metadata

**Analog search scope:**
- `app/` — все 5 layers (routers, services, models, schemas, utils)
- `migrations/` — 012_workspace.sql как прямой DDL аналог
- `tests/` — Phase 1 тесты как integration-test pattern
- `.planning/codebase/` — STRUCTURE.md, CONVENTIONS.md, TESTING.md
- `.planning/phases/02-tg-accounts-contacts/02-RESEARCH.md` — полностью

**Files scanned:** 17 (новые/изменяемые) + ~25 (аналоги для копирования паттернов)

**Pattern extraction date:** 2026-05-21

**Quality flags:**
- All 17 файлов имеют либо exact, либо role-match analog
- 4 NEW pattern'а явно помечены и снабжены полным skeleton'ом из RESEARCH.md
- Все 12 anti-patterns из CONCERNS.md + RESEARCH.md задокументированы с точными file:line

*Phase: 02-tg-accounts-contacts*
*Pattern map: 2026-05-21*
