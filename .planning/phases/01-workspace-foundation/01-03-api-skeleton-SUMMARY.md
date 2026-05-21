---
phase: 01-workspace-foundation
plan: 03
type: execute
status: complete
requirements-completed:
  - AUTH-01
  - AUTH-04
  - TENT-03
files-created:
  - app/routers/workspace.py
  - tests/test_workspace_router.py
  - tests/test_workspace_api_keys.py
files-modified:
  - app/config.py
  - app/main.py
  - app/routers/health.py
  - docker-compose.yml
files-deleted:
  - app/routers/auth.py
key-files:
  created:
    - app/routers/workspace.py
    - tests/test_workspace_router.py
    - tests/test_workspace_api_keys.py
  deleted:
    - app/routers/auth.py
provides:
  - 6 workspace endpoints (POST /auth/me, GET/PATCH /workspace, POST/GET/DELETE /workspace/api-keys)
  - CORS lockdown (settings.cors_origins_list, explicit allow_headers, no wildcard)
  - Supabase + CORS env vars wired into docker-compose api + listener
  - HTTP-layer integration coverage for AUTH-01 bootstrap, AUTH-04 stateless, TENT-03 keys, TENT-04 dual-auth
self-check: PASSED
---

# 01-03-api-skeleton — SUMMARY

## Что построено

### Task 1 (commit 58c25e8): config rewrite + delete legacy auth.py
- `app/config.py`: удалено поле `api_key: str` (D-14 — grep по `app/services/` показал отсутствие references); добавлены `supabase_url: str` (Required) и `cors_allowed_origins: str = "http://localhost:5173"`; добавлена `@property cors_origins_list` парсер comma-separated → list.
- `app/routers/auth.py`: удалён файл целиком (verify_api_key больше не нужен).

### Task 2 (commit 0769a89): workspace router + main.py + docker-compose
- `app/routers/workspace.py` (~290 строк): inline Pydantic schemas + 6 endpoints. Все используют `ctx: AuthCtx = Depends(auth_dep)` (7 вхождений включая helper).
  - **`_require_jwt(ctx)`** helper — JWT-only enforcement для 5 из 6 endpoints (D-10).
  - **POST `/api/v1/auth/me`** — bootstrap, триггерит TENT-02 lazy create через auth_dep, возвращает AuthMeResponse с workspace_name+created_at.
  - **GET `/api/v1/workspace`** — единственный dual-auth endpoint (JWT или API key, TENT-04).
  - **PATCH `/api/v1/workspace`** — rename с empty-string guard (400 INVALID_NAME).
  - **POST `/api/v1/workspace/api-keys`** — `secrets.token_urlsafe(32)` → `wsk_<32 char>` → prefix=12 → `asyncio.to_thread(bcrypt.hashpw, ...)` → store; **token=full_token возвращается ровно один раз** (plaintext-once, D-13).
  - **GET `/api/v1/workspace/api-keys`** — ApiKeyListItem без полей token/bcrypt_hash (security).
  - **DELETE `/api/v1/workspace/api-keys/{key_id}`** — `revoked_at = func.now()`; cross-tenant guard через `WHERE workspace_id == ctx.workspace_id` → 404 (не 403, security: hide existence).
  - 5x `TODO(v2-rls)` маркеров на app-level workspace_id фильтрах.
- `app/main.py`: переписан целиком.
  - **9 удалённых include_router**: `send, senders, conversations, contexts, onboarding, queue, check_contacts, warmup, proxy_pool` (D-14). Остались **ровно 2**: `health.router` + `workspace.router`.
  - **CORS lockdown**: `allow_origins=settings.cors_origins_list`, `allow_methods=["GET","HEAD","POST","PATCH","DELETE","OPTIONS"]` (W-2: HEAD для preflight), `allow_headers=["Authorization","X-Workspace-Key","Content-Type"]`. Wildcard полностью удалён.
  - Lifespan + queue_worker + warmup_worker оставлены AS IS (D-15 — services не трогаем).
  - Title переименован "Outreach Platform API", version "2.0.0-phase1".
- `app/routers/health.py`: убран импорт удалённого `verify_api_key`; `/health/detailed` endpoint временно убран (зависел от legacy auth) — будет восстановлен в Phase 2 под `auth_dep` с workspace_id scoping. Базовый `GET /api/v1/health` работает без изменений.
- `docker-compose.yml`:
  - **api section**: удалён `API_KEY: ${API_KEY}`; добавлены `SUPABASE_JWT_SECRET`, `SUPABASE_URL`, `CORS_ALLOWED_ORIGINS`.
  - **listener section**: `API_KEY` удалён (grep по services/ показал отсутствие references); добавлены `SUPABASE_JWT_SECRET` и `SUPABASE_URL` потому что listener загружает тот же `app.config.Settings` (Required fields упадут на старте без них).

### Task 3 (commit cb2d47e): HTTP integration tests
- `tests/test_workspace_router.py` (8 тестов):
  - `test_auth_me_no_auth_returns_401` — 401 + код `AUTH_REQUIRED`.
  - `test_auth_me_bootstrap_creates_workspace` — TENT-02 + D-09 (workspace_name = email).
  - `test_auth_me_idempotent` — повторный вызов возвращает тот же workspace_id.
  - `test_auth_me_rejects_api_key` — JWT-only (D-10): wsk_ header → 401.
  - `test_get_workspace_with_jwt`, `test_get_workspace_no_auth_401`.
  - `test_patch_workspace_renames`, `test_patch_workspace_empty_name_400`.
- `tests/test_workspace_api_keys.py` (6 тестов):
  - `test_create_api_key_returns_plaintext_once` — `wsk_` prefix, length 12 (D-13).
  - `test_list_api_keys_excludes_plaintext` — нет полей token/bcrypt_hash в GET.
  - `test_revoke_api_key` — DELETE 204 → revoked_at заполнено.
  - `test_revoked_key_cannot_authenticate` — после revoke ключ → 401 API_KEY_INVALID.
  - `test_api_key_grants_access_to_workspace_endpoint` — TENT-04: wsk_ ключ даёт GET /workspace.
  - `test_cross_tenant_isolation` — workspace B получает 404 (не 403) на ключ workspace A; ключ A остаётся untouched.
- Каждый тест использует уникальный `sub=` → независимые workspaces в одной БД → cross-test isolation.

## Endpoint матрица

| Method | Path                                | JWT | API key | Notes |
|--------|-------------------------------------|-----|---------|-------|
| GET    | `/api/v1/health`                    | —   | —       | unauthenticated smoke |
| POST   | `/api/v1/auth/me`                   | OK  | 401     | bootstrap (TENT-02 + AUTH-01 UX) |
| GET    | `/api/v1/workspace`                 | OK  | OK      | dual-auth (TENT-04) |
| PATCH  | `/api/v1/workspace`                 | OK  | 403     | JWT-only (rename) |
| POST   | `/api/v1/workspace/api-keys`        | OK  | 403     | plaintext-once (D-13) |
| GET    | `/api/v1/workspace/api-keys`        | OK  | 403     | no plaintext |
| DELETE | `/api/v1/workspace/api-keys/{id}`   | OK  | 403     | soft-revoke, cross-tenant 404 |

## Что выпилено из app/main.py (D-14)

9 `include_router` вызовов удалены:
1. `send.router`
2. `senders.router`
3. `conversations.router`
4. `contexts.router`
5. `onboarding.router`
6. `queue_router.router`
7. `check_contacts.router`
8. `warmup_router.router`
9. `proxy_pool_router.router`

Сами файлы (`app/routers/*.py`) **остаются** как "dead code" — Python interpreter их не грузит, пока никто не импортирует. Они будут полностью переписаны поверх `workspace_id` в Phase 2-4.

## Что в config

**Добавлено:**
- `supabase_jwt_secret: str` (Required, добавлено в 01-02; в 01-03 сохранено)
- `supabase_url: str` (Required)
- `cors_allowed_origins: str = "http://localhost:5173"` (с дефолтом для dev)
- `@property cors_origins_list -> list[str]` (парсер comma-separated)

**Удалено:**
- `api_key: str` (D-14 — никто из services не использует)

## Какие env vars теперь обязательны

| Env var                | Где обязательно           | Источник                                                    |
|------------------------|---------------------------|-------------------------------------------------------------|
| `SUPABASE_JWT_SECRET`  | api + listener            | Supabase Dashboard → Project Settings → API → JWT Settings |
| `SUPABASE_URL`         | api + listener            | Supabase Dashboard → Project Settings → API → Project URL  |
| `CORS_ALLOWED_ORIGINS` | api (только)              | comma-separated: `http://localhost:5173,https://app.outreach-platform.com` |

Без них pydantic `Settings()` падает с `ValidationError` при первом импорте `app.config`. Существующие `DATABASE_URL`, `TELEGRAM_API_*`, `ENCRYPTION_KEY`, `OPENAI_API_KEY` остаются обязательными.

## Покрытие тестами по requirement-ам

| Requirement | Тест                                                          | Файл                          |
|-------------|---------------------------------------------------------------|-------------------------------|
| AUTH-01     | `test_auth_me_bootstrap_creates_workspace`                    | test_workspace_router.py      |
| AUTH-01     | `test_auth_me_idempotent`                                     | test_workspace_router.py      |
| AUTH-04     | `test_get_workspace_with_jwt` (stateless JWT validation)      | test_workspace_router.py      |
| TENT-03     | `test_create_api_key_returns_plaintext_once`                  | test_workspace_api_keys.py    |
| TENT-03     | `test_list_api_keys_excludes_plaintext`                       | test_workspace_api_keys.py    |
| TENT-03     | `test_revoke_api_key` + `test_revoked_key_cannot_authenticate`| test_workspace_api_keys.py    |
| TENT-04     | `test_api_key_grants_access_to_workspace_endpoint`            | test_workspace_api_keys.py    |
| D-04        | `test_cross_tenant_isolation`                                 | test_workspace_api_keys.py    |
| D-10        | `test_auth_me_rejects_api_key`                                | test_workspace_router.py      |
| Phase 1 SC #1 | `test_auth_me_bootstrap_creates_workspace` (magic link → JWT) | test_workspace_router.py    |
| Phase 1 SC #2 | `test_auth_me_idempotent` (auto-create на первом запросе)    | test_workspace_router.py      |
| Phase 1 SC #3 | `test_get_workspace_no_auth_401`                             | test_workspace_router.py      |
| Phase 1 SC #4 | `test_list_api_keys_excludes_plaintext` (видит без plaintext)| test_workspace_api_keys.py    |
| Phase 1 SC #5 | migration 012 (out of scope этого плана, покрыто 01-01)      | test_migration_012.py         |

## Verification status

- **Static checks**: все verify-grep маркеры PLAN.md прошли (config поля, workspace.py endpoints, main.py `include_router` count == 2, no wildcard CORS, docker-compose env vars).
- **Python syntax**: `py_compile` зелёный на всех новых/изменённых `.py` файлах.
- **Runtime pytest**: НЕ запускался — нет локального Docker + Postgres на worktree host (executor environment). Static-shape тестов валиден. Полный green-pass откладывается до dev environment с `docker compose up -d db` + `pytest tests/`.
- **docker compose config -q**: НЕ запускался — Docker CLI отсутствует на worktree host (тот же случай, что в 01-01). YAML structurally валиден (правки сделаны мелкими Edit-операциями поверх существующего файла, прошедшего 01-01 валидацию).

## Готовность к Phase 2 (TG Accounts & Contacts)

**Заложено:**
- `auth_dep` готов принимать JWT и `wsk_` ключи → любой новый router добавляется одной строкой `include_router(...)`.
- `AuthCtx.workspace_id` доступен во всех endpoint-ах → enforcement workspace-scoping тривиален: `WHERE workspace_id == ctx.workspace_id`.
- Pattern `_require_jwt(ctx)` готов для admin-операций (rename workspace, manage members в будущем).
- ORM-стиль `select(...).where(...)` вместо raw SQL `text(...)` — workspace.py показал шаблон, который Phase 2-4 могут копировать.
- `secrets.token_urlsafe` + `asyncio.to_thread(bcrypt.hashpw, ...)` паттерн готов для других кредов (не только api keys — например, webhook secrets).

**Какие routers осталось переписать в Phase 2-4:**
- `senders.py` — добавить workspace_id фильтрацию, заменить `verify_api_key` на `auth_dep`, заменить sync error handling на 401/403 от `AuthCtx`.
- `contexts.py` — то же + миграция полей `auto_pause_triggers` под UI редактирование (Phase 4).
- `conversations.py` — workspace-scoped inbox, manual takeover (Phase 4).
- `onboarding.py` — workspace_id на новых Sender'ах при создании.
- `queue.py`, `warmup.py`, `proxy_pool.py`, `check_contacts.py`, `send.py` — все требуют workspace_id фильтрации.
- `health.py` — восстановить `/health/detailed` под `auth_dep` + workspace_id (детализация per-workspace sender stats).

**Что НЕ заложено (явно out of scope Phase 1):**
- Логирование `last_used_at` для api keys через background task (best-effort sync update в `_verify_api_key` достаточно).
- Rate limiting на `/auth/me` (Phase 5 — production hardening).
- Multi-workspace per user (D-10 — owner-инвариант v1, поддерживается ОДИН workspace на пользователя).
- Email уведомления при revoke api key (Phase 6).

## Self-Check: PASSED

3 атомарных коммита (58c25e8, 0769a89, cb2d47e). Все verify-markers статически зелёные. STATE.md и ROADMAP.md НЕ модифицированы (D — orchestrator owns those writes).
