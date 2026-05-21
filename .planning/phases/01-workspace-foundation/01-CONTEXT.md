# Phase 1: Workspace Foundation - Context

**Gathered:** 2026-05-21
**Status:** Ready for planning

<domain>
## Phase Boundary

Заложить мультитенантный фундамент outreach-platform: схема БД с `workspace_id` на всех арендатор-скоупленных таблицах, валидация Supabase magic-link JWT в FastAPI с автосозданием workspace при первом входе, скелет нового API-слоя с двойной аутентификацией (JWT для UI + Workspace API-ключ для n8n).

**В скоупе:** миграция БД, AuthDep middleware, новые роутеры workspace/auth, удаление старого X-API-Key middleware и старых роутеров.

**Не в скоупе (для последующих фаз):** перезапись бизнес-роутеров (send/senders/contexts/conversations/onboarding и т.д.), UI клиента для прокси, RLS на уровне Postgres, переключение active workspace для пользователя.

</domain>

<decisions>
## Implementation Decisions

### БД и стратегия миграции

- **D-01:** Стартовое состояние БД outreach-platform — пустая, новый Postgres-контейнер в собственном `docker-compose.yml`. Данные из prod telegram-api не переносятся. AGS Foods заводится заново как обычный workspace при необходимости.
- **D-02:** Миграция `012_workspace.sql` добавляет `workspace_id UUID NOT NULL` FK на всех арендатор-скоупленных таблицах в одной транзакции. Без nullable-фазы, без backfill — БД пустая.
- **D-03:** Все ресурсные таблицы scoped по workspace, **включая `proxy_pool` и `warmup_pool`**. Модель BYO-proxy: клиент сам приносит свои прокси. Warmup происходит только между аккаунтами одного workspace (никогда между разными клиентами).
- **D-04:** Изоляция enforced на уровне приложения: helper `get_db_scoped(workspace_id)` или явный `.where(Model.workspace_id == ctx.workspace_id)` в каждом репо-запросе. DB-уровень: `NOT NULL` FK + композитные индексы `(workspace_id, ...)` где они нужны для существующих запросов. **Postgres RLS отложен на v2** — оставить TODO-метки в коде там, где могла бы быть RLS-политика.

### Supabase JWT валидация и источник workspace_id

- **D-05:** Валидация JWT — локально, HS256 через `python-jose` (уже в requirements.txt). `SUPABASE_JWT_SECRET` берётся из project settings → API в Supabase, добавляется в `app/config.py` как новое поле. Никаких HTTP-вызовов в Supabase на запрос, никакого JWKS-кэша.
- **D-06:** `workspace_id` НЕ хранится в JWT claims. Источник — новая локальная таблица `user_workspaces (supabase_user_id text, workspace_id UUID, role enum, created_at)` с индексом по `supabase_user_id`. AuthDep декодит JWT → берёт `sub` → `SELECT workspace_id, role FROM user_workspaces WHERE supabase_user_id = $1`.
- **D-07:** Никакого in-memory кэша `user_id → workspace_id` в v1. Простой SELECT с индексом достаточно быстр; кэш добавим только если профайл покажет узкое место.

### Auto-create workspace при первом входе

- **D-08:** Lazy-создание в FastAPI AuthDep: валидный JWT + пустой lookup в `user_workspaces` → создать `workspace` + `user_workspaces` в одной DB-транзакции, вернуть AuthCtx с новым workspace_id. Lovable ничего об этом не знает — просто дёргает API после signup.
- **D-09:** Имя нового workspace по умолчанию = email пользователя из JWT claim (или `'My Workspace'` если email отсутствует). Пользователь переименует через `PATCH /api/v1/workspace`.
- **D-10:** Схема `user_workspaces` сразу many-to-many с `role` enum (`owner`/`admin`/`member`), **без UNIQUE** на `supabase_user_id`. В v1 бизнес-инвариант: 1 user = 1 workspace (lookup возвращает первый/единственный). v2 — добавим выбор active workspace через header `X-Workspace-Id` или custom JWT claim; миграции не потребуется.

### Dual auth: JWT (UI) + Workspace API-ключ (n8n)

- **D-11:** Один FastAPI Depends — `AuthDep` в `app/utils/auth.py` (новый файл). Ветвится по заголовку:
  - `Authorization: Bearer <token>` → декодим как Supabase JWT → lookup workspace_id
  - `X-Workspace-Key: wsk_<random>` → парсим prefix → bcrypt-проверка → workspace_id из строки ключа
  - Ни тот, ни другой → 401
- **D-12:** Возвращаемый объект — `AuthCtx(workspace_id: UUID, user_id: str | None, source: Literal['jwt','api_key'], role: str | None)`. Все новые роутеры принимают `ctx: AuthCtx = Depends(auth_dep)`.
- **D-13:** Workspace API-ключ:
  - Формат токена `wsk_` + 32 url-safe random bytes (через `secrets.token_urlsafe`).
  - В БД хранится: `workspace_api_keys(id, workspace_id FK, prefix VARCHAR(12), bcrypt_hash TEXT, name VARCHAR(50), created_at, last_used_at, revoked_at)`. Plaintext-токен пользователь видит ровно один раз в ответе POST-создания.
  - Lookup: парсим prefix → `SELECT * WHERE prefix=$1 AND revoked_at IS NULL` → bcrypt.verify над кандидатами.
  - Регенерация = revoke + create new. У одного workspace может быть несколько активных ключей одновременно.
- **D-14:** Старый `verify_api_key` (X-API-Key) **полностью удаляется**. Все 11 старых роутеров **выпиливаются из `app/main.py`** (файлы можно оставить как заготовки для рерайта в Phase 2-4, но `include_router` убирается). После Phase 1 продукт не отвечает на бизнес-запросы — только на новые workspace/auth-эндпоинты. Это сознательное решение: внешних клиентов ещё нет, AGS Foods продолжает работать в `/root/apps/telegram-api/` независимо.
- **D-15:** `app/services/` (бизнес-логика — queue.py, listener.py, telegram.py, ai_engine.py и т.д.) **не трогается** в Phase 1. Когда роутеры будут переписываться в Phase 2-4, в каждый service-вызов добавится фильтр `workspace_id`.

### Тесты, runtime изоляция (добавлено после research 2026-05-21)

- **D-16:** Supabase JWT алгоритм — **HS256** (D-05 подтверждён). У клиента legacy-проект Supabase или JWT-настройки переключены на HS256 в dashboard. `SUPABASE_JWT_SECRET` берётся из Settings → API. PyJWT/JWKS не нужны.
- **D-17:** Phase 1 включает **Wave 0: установка pytest-инфраструктуры с нуля**. Добавить `pytest`, `pytest-asyncio`, `httpx` (для AsyncClient тестов FastAPI) в `requirements.txt` (либо `requirements-dev.txt` если planner предпочтёт разделить). Создать `tests/conftest.py` с фикстурами: `async_db_session` (изолированная транзакция per-test), `valid_supabase_jwt` (фабрика, генерирует JWT с заданными claims через `python-jose` + `SUPABASE_JWT_SECRET`), `async_client` (`httpx.AsyncClient` с тестовым FastAPI app). Покрытие — миграция 012 (smoke + все 11 таблиц имеют `workspace_id`) + auth_dep (валидный JWT / невалидный JWT / отсутствие заголовка / валидный API-ключ / отозванный API-ключ / lazy-create workspace при первом входе).
- **D-18:** `docker-compose.yml` outreach-platform — **container_name переименовать** на `outreach-platform-db`, `outreach-platform-api`, `outreach-platform-listener`. Имена `telegram-api-*` заняты прод-сервисом в `/root/apps/telegram-api/`; запуск outreach-platform с теми же именами на том же VPS убъёт прод. Сервисные имена в `docker-compose.yml` (`services:` ключи) тоже стоит переименовать для консистентности.

### Claude's Discretion

- **C-01:** Точное имя нового AuthCtx/AuthDep файла (`app/utils/auth.py` рекомендовано) — planner может выбрать другое имя в рамках конвенций кодбазы.
- **C-02:** Точный формат `workspace_api_keys.prefix` — 8 или 12 символов, как индексировать. Planner подберёт.
- **C-03:** Список endpoint-ов workspace-скелета (минимум: `POST /api/v1/auth/me` для bootstrap, `GET /api/v1/workspace`, `PATCH /api/v1/workspace`, `POST /api/v1/workspace/api-keys`, `GET /api/v1/workspace/api-keys`, `DELETE /api/v1/workspace/api-keys/{id}`) — researcher уточнит по REQUIREMENTS.md.
- **C-04:** Решение по `init_db()` `Base.metadata.create_all` в `app/database.py` — оставить как есть или заменить на migration runner. Pattern уже задокументирован как tech debt в `.planning/codebase/CONCERNS.md`. Planner решит, нужно ли фиксить в Phase 1 или отложить.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project-level
- `CLAUDE.md` — главные правила проекта: запрет Alembic, raw SQL миграции 012_+, async everywhere, общение на русском
- `.planning/PROJECT.md` — Key Decisions (Supabase magic link, per-agent settings, full API rewrite, brownfield)
- `.planning/REQUIREMENTS.md` — TENT-01..04 и AUTH-01..04, ровно те требования, что должен закрыть Phase 1
- `.planning/ROADMAP.md` §"Phase 1" — Success Criteria и состав плана (01-01 миграция, 01-02 middleware, 01-03 API скелет)

### Codebase intel
- `.planning/codebase/ARCHITECTURE.md` — слойная разбивка router→service→data, текущая single-tenant auth, отсутствие workspace_id
- `.planning/codebase/STRUCTURE.md` — куда класть новые файлы (`app/utils/auth.py`, `app/routers/workspace.py`), как именовать миграции, паттерн нового роутера
- `.planning/codebase/INTEGRATIONS.md` §"Authentication & Identity" — текущий `verify_api_key` через X-API-Key, заметка что python-jose уже установлен
- `.planning/codebase/CONCERNS.md` §"Single global API key" + §"No workspace/tenant isolation" — главные tech-debt пункты, которые Phase 1 закрывает

### Существующий код (читать перед изменением)
- `app/main.py` — точка регистрации роутеров и CORS middleware
- `app/routers/auth.py` — текущий `verify_api_key` (полностью удаляется)
- `app/config.py` — `Settings` через pydantic-settings, сюда добавляются `SUPABASE_JWT_SECRET`, `SUPABASE_URL`
- `app/database.py` — `AsyncSessionLocal`, `get_db()`, спорный `Base.metadata.create_all` в `init_db()`
- `app/models/__init__.py` — все ORM-модели в одном файле, паттерн PascalCase + UUID PK
- `migrations/` 001-011 — паттерн raw SQL с `IF NOT EXISTS`, следующая 012_

### Supabase (внешний)
- Supabase project settings → API → JWT Secret — источник `SUPABASE_JWT_SECRET` env-переменной
- Supabase project settings → API → Project URL — источник `SUPABASE_URL`

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `python-jose` уже в `requirements.txt` — не нужно добавлять зависимость для JWT-декодинга
- `bcrypt` есть транзитивно через `passlib`/FastAPI dependencies — проверить наличие, иначе добавить
- Паттерн `Depends(verify_api_key)` во всех существующих роутерах — drop-in замена на `Depends(auth_dep)` когда роутеры будут переписываться в Phase 2-4
- `secrets.token_urlsafe(32)` стандартная библиотека — генерация API-ключа
- `app/utils/` пустой — идеальное место для `auth.py` (валидация JWT, AuthCtx, AuthDep)
- `AsyncSessionLocal` + `get_db()` в `app/database.py` — паттерн для всех новых DB-зависимостей

### Established Patterns
- Все таблицы: UUID PK через `default=uuid.uuid4`, `server_default` для timestamp'ов
- Миграции — raw SQL, идемпотентные (`IF NOT EXISTS`/`IF EXISTS`), нумерация `NNN_description.sql`
- Async-only: `AsyncSession`, никаких sync операций
- Логирование через `logging.getLogger(__name__)`, без `print()`
- Pydantic v2: `model_config = ConfigDict(...)`, `model_validator`
- Все enum'ы в моделях — Python `enum.Enum` + `SQLEnum(...)` (не `String`-поля; см. CONCERNS.md про unconstrained `Sender.role`)

### Integration Points
- **`app/main.py`** — добавить `app.include_router(workspace_router)`; убрать все `include_router` старых роутеров
- **`app/config.py`** — расширить `Settings`: `supabase_jwt_secret: str`, `supabase_url: str`, удалить `api_key`
- **`docker-compose.yml`** — добавить новые env vars в `api` и `listener` секции
- **`app/database.py`** — Base.metadata.create_all (спорный) либо остаётся либо заменяется на runner — см. C-04

### Anti-pattern, который НЕ повторять (из CONCERNS.md)
- `Sender.role` хранится как `String(20)` без CHECK — для новых полей (роль в `user_workspaces`) используем `SQLEnum`
- Onboarding state в in-memory dict (`_onboarding_sessions`) — НЕ хранить никакой in-flight auth state в памяти процесса; всё в БД или браузере (Supabase session уже там)
- `subprocess.run(["docker", "restart", ...])` в API-роутере — никогда так не делать в новом коде
- CORS `allow_origins=["*"]` — при рерайте `main.py` ограничить до Lovable-домена через env

</code_context>

<specifics>
## Specific Ideas

- **AGS Foods workspace** не привилегирован: создаётся как обычный workspace когда нужно. Никаких хардкод-данных "AGS Internal" в код.
- **Workspace API-ключ — формат токена**: `wsk_` префикс намеренно — узнаваемо в логах, поиске и при копи-пасте (Stripe-стиль `sk_`, GitHub `ghp_`). Облегчает grep по логам.
- **Имя workspace по умолчанию = email** — пользователь увидит "его" workspace, переименует если нужно.
- **Phase 1 продукт частично нерабочий после деплоя** — это OK. Старая `telegram-api` обслуживает прод, новая `outreach-platform` ещё не имеет бизнес-эндпоинтов. Phase 2-4 их восстанавливают поверх workspace_id.

</specifics>

<deferred>
## Deferred Ideas

### Для Phase 2
- UI для загрузки клиентом своих прокси в workspace pool (BYO-proxy следствие D-03)
- Перепись `app/routers/senders.py` + `app/routers/onboarding.py` поверх workspace_id (привязка senders к workspace)
- Решение что делать со `subprocess.run(["docker", "restart"])` в senders.py — likely заменить на DB-flag + listener-poll

### Для Phase 3 / Phase 4
- Перезапись `send.py`, `conversations.py`, `contexts.py`, `queue.py` (router), `check_contacts.py`, `warmup.py` (router), `proxy_pool.py` поверх workspace_id и AuthCtx

### Для v2
- **Postgres RLS** (Row-Level Security) на всех арендатор-скоупленных таблицах. Сейчас в коде оставить TODO-комментарии у каждого `.where(workspace_id == ...)` фильтра: `# TODO(v2-rls): replaced by RLS policy app.workspace_id`
- **Выбор active workspace** для пользователя с несколькими workspace'ами (header `X-Workspace-Id` или custom JWT claim) — схема уже many-to-many, миграция не потребуется
- **Custom JWT claim `workspace_id`** через Supabase Edge Function — устранит DB lookup на каждый запрос когда нагрузка вырастет
- **In-memory кэш user_id → workspace_id** в API-процессе с TTL 5 мин — только если профайл покажет узкое место
- **Team support** (TEAM-01, TEAM-02): приглашение участников в workspace по email, роли admin/member уже заложены в schema через D-10

### Tech debt, обнаруженный во время обсуждения
- `init_db()` `Base.metadata.create_all` в `app/database.py` противоречит raw-SQL миграциям — либо удалить, либо превратить в migration runner. Решение оставлено за planner'ом (C-04), но в любом случае фиксить нужно.

</deferred>

---

*Phase: 1-workspace-foundation*
*Context gathered: 2026-05-21*
