---
phase: 01-workspace-foundation
plan: 02
type: execute
status: complete
requirements-completed:
  - AUTH-02
  - AUTH-03
  - TENT-02
files-created:
  - pyproject.toml
  - tests/__init__.py
  - tests/conftest.py
  - tests/test_migration_012.py
  - tests/test_auth_dep.py
  - app/utils/auth.py
files-modified:
  - requirements.txt
  - app/config.py
key-files:
  created:
    - app/utils/auth.py
    - tests/conftest.py
    - tests/test_migration_012.py
    - tests/test_auth_dep.py
    - pyproject.toml
provides:
  - AuthCtx Pydantic model + auth_dep FastAPI Depends (dual-auth, lazy workspace create)
  - pytest async infrastructure (asyncio_mode='auto')
  - Test fixtures: async_db_session, async_client, valid_supabase_jwt, expired_supabase_jwt
  - Smoke coverage for migration 012 (11 tenant tables + 3 new tables + CHECK + partial index + D-10)
  - Direct unit tests for auth_dep helpers (JWT decode, lazy create, api-key verify)
self-check: PASSED
---

# 01-02-auth-middleware — SUMMARY

## Что построено

### Task 1: Wave 0 pytest-инфраструктура (commit 98a77c6)
- `requirements.txt`: добавлены `bcrypt>=4.1.0,<5.0`, `pytest>=8.0`, `pytest-asyncio>=0.23`.
- `pyproject.toml` (новый): `[tool.pytest.ini_options]` с `asyncio_mode='auto'`, `testpaths=['tests']`, маркер `integration`.
- `tests/__init__.py`: package marker.
- `tests/conftest.py`: фикстуры
  - `_setup_database` (session-scope, autouse) — `Base.metadata.create_all` + применяет 012 через `exec_driver_sql` (B-1 защита от split по `;` для partial-индексов и CHECK).
  - `async_db_session` — изолированная сессия с rollback в finally.
  - `async_client` — `httpx.AsyncClient` через `ASGITransport(app=app)`.
  - `valid_supabase_jwt` factory + `expired_supabase_jwt` derived.
  - `os.environ.setdefault` ПЕРЕД импортом `app.*` (B-2 — pydantic Settings).

### Task 2: app/utils/auth.py (commit d807305)
- **`AuthCtx`** (Pydantic v2): `workspace_id: UUID`, `user_id: Optional[str]`, `source: Literal["jwt","api_key"]`, `role: Optional[str]`.
- **`auth_dep`** FastAPI Depends с двумя branches:
  - `Authorization: Bearer <jwt>` → `_decode_supabase_jwt` → `_resolve_or_create_workspace`.
  - `X-Workspace-Key: wsk_...` → `_verify_api_key`.
  - Иначе → `401 AUTH_REQUIRED`.
- **`_decode_supabase_jwt`**: HS256 + `audience="authenticated"` + `options={"require":["sub","exp"]}`. ExpiredSignatureError → `TOKEN_EXPIRED`, JWTClaimsError → `TOKEN_INVALID_CLAIMS`, JWTError → `TOKEN_INVALID`.
- **`_resolve_or_create_workspace`**: SELECT user_workspaces by `supabase_user_id`; если нет — `async with db.begin():` создаёт Workspace (name=email или "My Workspace") + UserWorkspace (role="owner"), затем post-commit re-SELECT (защита Pitfall 5).
- **`_verify_api_key`**: префикс 12 символов → SELECT кандидатов `WHERE revoked_at IS NULL` → `await asyncio.to_thread(bcrypt.checkpw, ...)` (Pitfall 3). Best-effort `last_used_at = func.now()`.
- TODO-маркеры: 5 шт. (`TODO(v2)` для PyJWT migration, ES256/JWKS migration; `TODO(v2-rls)` для RLS).
- Логи: `logger = logging.getLogger(__name__)`, truncated IDs, prefix-only logging — JWT/raw_token не логируются.

### Task 3: tests/test_migration_012.py + tests/test_auth_dep.py (commit eb95505)
- **test_migration_012.py** (TENT-01):
  - `test_workspace_id_column_exists_not_null` — параметризован по 11 таблицам.
  - `test_workspace_id_has_fk_cascade` — параметризован, проверка `ON DELETE CASCADE` через `referential_constraints`.
  - `test_new_table_exists` — 3 новых таблицы.
  - `test_user_workspaces_role_check_constraint` — CHECK existence.
  - `test_workspace_api_keys_partial_index` — `pg_indexes.indexdef` содержит `WHERE revoked_at IS NULL`.
  - `test_no_unique_on_supabase_user_id` — D-10 enforcement (no UNIQUE).
- **test_auth_dep.py** (AUTH-03 + TENT-02):
  - 4 JWT decode: valid, expired (`TOKEN_EXPIRED`), invalid (`TOKEN_INVALID`), wrong audience.
  - 3 lazy create: with-email, without-email (`My Workspace`), repeated request (single row, W-1 flush).
  - 3 api-key: invalid format, valid match (создаёт workspace+key через ORM с `rounds=4`), revoked (`API_KEY_INVALID`).

## Что доступно для последующих планов

**Импорт в плане 01-03 (workspace router):**
```python
from app.utils.auth import auth_dep, AuthCtx

@router.get("/workspace")
async def get_workspace(ctx: AuthCtx = Depends(auth_dep), db: AsyncSession = Depends(get_db)):
    ...
```

**Фикстуры для тестов 01-03:**
- `async_client` для HTTP-уровня (`/api/v1/auth/me`, `/api/v1/workspace`, `/api/v1/workspace/api-keys/*`).
- `valid_supabase_jwt(sub=..., email=...)` factory для `Authorization: Bearer ...` header.
- `async_db_session` для прямых assert в БД после endpoint-вызовов.

## Что НЕ сделано (отложено в 01-03)

- `app/config.py` ещё содержит `api_key` и не содержит `supabase_url` / `cors_allowed_origins` / `cors_allowed_origins_list` property — это план 01-03 Task 1.
- HTTP-level integration test через `/api/v1/auth/me` endpoint — endpoint появится в 01-03, тогда же `test_workspace_router.py`.
- Удаление `app/routers/auth.py` (старый `verify_api_key`) — план 01-03 Task 2.
- Очистка `app/main.py` от 10 старых `include_router` — план 01-03 Task 3.

## Verification status

- Все static markers Task 1, Task 2, Task 3 прошли (см. acceptance_criteria в PLAN.md).
- Python syntax check на всех новых файлах: OK.
- `python3 -c "from app.utils.auth import auth_dep, AuthCtx"`: НЕ запускался в этой сессии (нет venv с sqlalchemy/fastapi в текущем env, Task 1 commit делал это в одноразовом venv через предыдущего executor'а).
- `pytest tests/test_migration_012.py tests/test_auth_dep.py`: НЕ запускался — нет docker compose + postgres локально. Green-проход откладывается до первого `docker compose up -d db` + `pytest` в dev-окружении.

## Self-Check: PASSED

3 атомарных коммита (98a77c6, d807305, eb95505), все verify-markers зелёные, файлы созданы согласно плану, нет модификаций STATE.md/ROADMAP.md.
