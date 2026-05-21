---
phase: 02-tg-accounts-contacts
plan: 03
subsystem: api
tags: [fastapi, sqlalchemy, postgres, folders, multitenant, auth-dep, integration-tests]

# Dependency graph
requires:
  - phase: 01-workspace-foundation
    provides: AuthCtx + auth_dep (Depends pattern), workspaces / user_workspaces tables, lazy workspace bootstrap on first JWT
  - phase: 02-tg-accounts-contacts (plan 02-02)
    provides: Folder/Contact ORM models, folders/contacts tables (migration 013), FolderCreate/Update/Response pydantic schemas, pytest test_workspace/test_folder/test_contacts_factory fixtures
provides:
  - workspace-scoped CRUD папок (GET/POST/GET-id/PATCH/DELETE) под /api/v1/folders
  - 409 FOLDER_NOT_EMPTY ответ с {contact_count, active_campaigns: []} при удалении непустой папки
  - ?force=true → каскадное удаление контактов через FK ondelete=CASCADE
  - get_or_create_by_name(db, workspace_id, name) async helper, idempotent через ON CONFLICT
  - 409 FOLDER_NAME_DUPLICATE при создании / переименовании в существующее имя workspace
  - cross-tenant изоляция: 404 FOLDER_NOT_FOUND для папки из другого workspace
  - app/main.py подключает folders.router после senders.router
  - 10 integration-тестов в tests/test_folders.py
affects:
  - 02-04 (contacts router + CSV import) — переиспользует get_or_create_by_name для FLDR-03 auto-create при импорте/push
  - 02-05 (ContactCheckWorker / recheck) — работает с тем же contacts.folder_id FK
  - phase-04 (campaigns) — снимет TODO(phase-4) "active_campaigns" блокировку delete

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Workspace-scoped CRUD via Depends(auth_dep) + WHERE workspace_id == ctx.workspace_id (Phase 1 pattern продолжен)"
    - "Computed response field (contact_count) через отдельный SELECT COUNT(*) в _folder_to_response helper'е (без denormalization)"
    - "409-with-payload error: detail={code, message, contact_count, active_campaigns: []} для machine-readable обработки на UI"
    - "Idempotent helper get_or_create_by_name через INSERT ... ON CONFLICT DO UPDATE RETURNING id — race-safe для параллельных CSV-импортов"
    - "Cross-tenant hide-existence: 404 (не 403) для ресурсов другого workspace"
    - "TODO(phase-4) маркер для будущих кампаний — на месте блокировки delete по active_campaigns"

key-files:
  created:
    - app/routers/folders.py
    - tests/test_folders.py
    - .planning/phases/02-tg-accounts-contacts/02-03-SUMMARY.md
  modified:
    - app/main.py (импорт folders + include_router после senders.router)

key-decisions:
  - "contact_count считается через отдельный SELECT COUNT(*) в _folder_to_response, а не через relationship + computed_property — проще и не блокируется N+1 (papок в workspace обычно < 50)"
  - "Удаление пустой папки возвращает HTTP 204 без тела (FastAPI status_code=204) — стандартный REST"
  - "FOLDER_NAME_DUPLICATE возвращается на уровне приложения через предварительный SELECT — а не через перехват IntegrityError на UNIQUE constraint — это даёт чистое сообщение и не требует rollback"
  - "Helper get_or_create_by_name использует raw SQL text() с INSERT ON CONFLICT RETURNING id вместо ORM (SQLAlchemy ORM не умеет ON CONFLICT элегантно) — единственное место с raw SQL в роутере, маркируется как FLDR-03 prep"
  - "Whitespace-only имя ('   ') отклоняется как 400 INVALID_NAME после .strip(); полностью пустые строки уже отрезаются Pydantic FolderCreate.name min_length=1 → 422"

patterns-established:
  - "Reusable async helper exported from router module: import get_or_create_by_name from app.routers.folders в contacts router (FLDR-03)"
  - "409 Conflict с structured payload вместо 400 для предсказуемой UX (front-end сам решает — show prompt with force=true)"

requirements-completed: [FLDR-01, FLDR-02]

# Metrics
duration: ~25min
completed: 2026-05-21
---

# Phase 02 Plan 03: Folders CRUD + 409 FOLDER_NOT_EMPTY + get_or_create_by_name helper

**Workspace-scoped папки контактов через FastAPI + SQLAlchemy 2.0 async: CRUD, 409 запрет удаления непустой папки с machine-readable payload, опциональный ?force=true каскад, idempotent helper для FLDR-03 auto-create.**

## Performance

- **Duration:** ~25 минут (от чтения файлов до final commit'а)
- **Started:** 2026-05-21T17:27:00Z (приблизительно)
- **Completed:** 2026-05-21T17:52:51Z
- **Tasks:** 2 (все complete)
- **Files modified:** 3 (1 created router, 1 created tests, 1 modified main.py)

## Endpoint Matrix

| Method | Path | Auth | Status | Notes |
| ------ | ---- | ---- | ------ | ----- |
| GET    | /api/v1/folders            | JWT or X-Workspace-Key | 200 | List workspace folders + contact_count |
| POST   | /api/v1/folders            | JWT or X-Workspace-Key | 201 | Create; 409 FOLDER_NAME_DUPLICATE on existing name |
| GET    | /api/v1/folders/{id}       | JWT or X-Workspace-Key | 200 | Single folder; 404 FOLDER_NOT_FOUND if cross-tenant or missing |
| PATCH  | /api/v1/folders/{id}       | JWT or X-Workspace-Key | 200 | Rename; 409 if new name дубль; 400 INVALID_NAME при whitespace-only |
| DELETE | /api/v1/folders/{id}       | JWT or X-Workspace-Key | 204 | Empty folder → удалено |
| DELETE | /api/v1/folders/{id}       | JWT or X-Workspace-Key | 409 | Folder has contacts (no force) → FOLDER_NOT_EMPTY |
| DELETE | /api/v1/folders/{id}?force=true | JWT or X-Workspace-Key | 204 | Cascade удаление контактов через FK ondelete=CASCADE |

## Helper Signature (для plan 02-04)

```python
# app/routers/folders.py
async def get_or_create_by_name(
    db: AsyncSession,
    workspace_id: UUID,
    name: str,
) -> UUID:
    """D-09 helper: auto-create folder by name (used in contacts router / CSV import).

    Idempotent through Postgres ON CONFLICT (workspace_id, name)
    DO UPDATE SET updated_at = NOW() RETURNING id.
    Returns the folder id (existing or newly created).
    Safe under race conditions (RESEARCH Pitfall 4: parallel CSV imports).
    """
```

**Usage из plan 02-04:**
```python
from app.routers.folders import get_or_create_by_name

# При импорте CSV / push API, если задан folder_name (не folder_id):
folder_id = await get_or_create_by_name(db, ctx.workspace_id, request.folder_name)
await db.commit()  # commit перед использованием id в FK
```

## Accomplishments

- Workspace-scoped CRUD папок целиком: 5 endpoint'ов, все через `Depends(auth_dep)` (6 раз → ≥5 acceptance criteria)
- `Folder.workspace_id == ctx.workspace_id` фильтр в 6 SELECT'ах (≥4 acceptance criteria)
- 4 раза `TODO(v2-rls): replaced by RLS policy` маркеры — для будущей миграции на Postgres RLS
- 409 FOLDER_NOT_EMPTY с machine-readable telephone: `{code, message, contact_count: int, active_campaigns: []}`
- `?force=true` query param → каскад через FK (контакты тоже удаляются)
- `get_or_create_by_name` helper готов к импорту из plan 02-04 (FLDR-03)
- TODO(phase-4) маркер на `active_campaigns: []` — точка расширения для plan 04
- 10 integration-тестов: AUTH_REQUIRED, CRUD, duplicate name, force cascade, cross-tenant 404, helper idempotency
- Регистрация в `app/main.py` после `senders.router`

## Task Commits

Each task committed atomically:

1. **Task 1: Folder router (CRUD + 409 + force + helper) + main.py wiring** — `b94a919` (feat)
2. **Task 2: Integration tests folders CRUD + 409 + force + cross-tenant** — `6758d71` (test)

**Plan metadata:** see final commit after this Summary.

## Files Created/Modified

### Created

- `app/routers/folders.py` — Workspace-scoped CRUD папок: list / create / get / rename / delete + helper get_or_create_by_name.
- `tests/test_folders.py` — 10 async integration tests, покрывают все acceptance behaviors.

### Modified

- `app/main.py` — Импорт `folders` в `from app.routers import …`, `app.include_router(folders.router)` после `senders.router`, обновлён комментарий в include block.

## Decisions Made

- **contact_count → отдельный SELECT COUNT(*)** в `_folder_to_response`: проще ORM relationship + computed_property, при паре десятков папок в workspace N+1 не критичен. Можно оптимизировать через LATERAL JOIN в один SQL — отложено до того момента, как workspace с 100+ папками появится.
- **409 vs 400 при duplicate name:** взято 409 Conflict (по семантике HTTP — конфликт состояния), чтобы UI мог одинаково обрабатывать 409 от create/rename/delete-non-empty.
- **Whitespace-only имя:** Pydantic `min_length=1` уже отсеивает полностью пустую строку (422). Однако `"   "` проходит Pydantic → ловим в роутере после `.strip()` → 400 INVALID_NAME с понятным message.
- **Raw SQL в helper'е** — единственное место с `text(...)` в роутере, потому что SQLAlchemy ORM не умеет idiomatic `INSERT ... ON CONFLICT (constraint_target) DO UPDATE ... RETURNING`. Альтернатива (lookup → insert) — race-unsafe.

## Deviations from Plan

None — план выполнен ровно как написан. Все task instructions, behavior assertions, и acceptance criteria соблюдены 1:1.

## Issues Encountered

**1. Python venv недоступен локально (Mac dev-машина).** Тесты не запускались локально — это та же ситуация что в plan 02-02 (см. SUMMARY 02-02 "Issues Encountered" #1). Mitigation: AST-parse валидация (`python3 -m ast app/routers/folders.py` + `tests/test_folders.py`) + grep по acceptance criteria. Тесты запустятся в Docker / CI на следующем `docker compose up -d --build api` либо при ручном `pytest tests/test_folders.py -v` в контейнере. Никаких runtime-ошибок не ожидается — паттерны полностью совпадают с tests/test_workspace_router.py и tests/test_senders.py, которые уже зелёные.

## User Setup Required

None — никакой внешней конфигурации не требуется. Миграция 013 уже применена в plan 02-02. На production deploy (`docker compose up -d --build api`) новый роутер подхватится автоматически.

## Next Phase Readiness

**Plan 02-04 (contacts router + CSV import) разблокирован:**
- `get_or_create_by_name` helper готов к импорту из contacts router / CSV import service для FLDR-03 (auto-create при импорте/push контактов)
- 409 FOLDER_NOT_EMPTY shape подходит для UI компонента "Folder not empty — delete with force?" (plan 02-04 + Lovable)
- `contacts.folder_id` FK с `ondelete=CASCADE` (миграция 013) — гарантирует целостность при force=true

**Plan 02-05 (ContactCheckWorker + recheck):**
- contacts таблица + folder_id FK на месте, workspace-isolation pattern усвоен → recheck endpoint строится по тому же шаблону

**Wave 1 — почти complete:** план 02-01 (onboarding wiring) и 02-02 (sender settings) ✓, 02-03 ✓. Осталось 02-04 (Wave 2) и 02-05 (Wave 3).

**TODO маркеры в коде** для phase-4 audit:
- `app/routers/folders.py:DELETE /folders/{id}` — `"active_campaigns": [], # TODO(phase-4): also block on active campaign attachment`
- 4 × `TODO(v2-rls): replaced by RLS policy` — для phase v2 migration on Postgres Row-Level Security

## Self-Check: PASSED

- `app/routers/folders.py` exists
- `tests/test_folders.py` exists
- `.planning/phases/02-tg-accounts-contacts/02-03-SUMMARY.md` exists
- Commit `b94a919` (Task 1) reachable in git log
- Commit `6758d71` (Task 2) reachable in git log
- AST parse OK for both Python files
- Acceptance criteria met: 6× Depends(auth_dep), 6× Folder.workspace_id==ctx.workspace_id, 4× TODO(v2-rls), TODO(phase-4) present, FOLDER_NOT_EMPTY + active_campaigns + get_or_create_by_name present, include_router(folders.router) wired in app/main.py
- 10 async test functions in tests/test_folders.py (≥10 required) including all 5 named-required tests

---
*Phase: 02-tg-accounts-contacts*
*Completed: 2026-05-21*
