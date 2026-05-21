# Phase 2: TG Accounts & Contacts - Context

**Gathered:** 2026-05-21
**Status:** Ready for planning

<domain>
## Phase Boundary

Phase 2 строит вокруг workspace-фундамента Phase 1 две новые предметные области:

1. **TG-аккаунты в workspace** — переписанный поверх `AuthCtx` flow онбординга
   (телефон → SMS → 2FA / QR), per-sender настройки (rate limits, прокси, lifecycle),
   live-статус каждого аккаунта в UI.
2. **База контактов с папками** — новая таблица `contacts` (workspace-level),
   модель `folders`, CSV-импорт с user-defined column mapping, push через
   Workspace API-ключ, асинхронная проверка наличия контактов в Telegram через
   sender с `role='checker'`.

**В скоупе:**
- Миграция `013_*` для `contacts`, `folders`, `onboarding_sessions`, новых полей
  Sender (rate_per_min/hour/day, lifecycle_status).
- Workspace-scoped рерайт роутеров `onboarding`, `senders`, `contacts`,
  `folders`, `check_contacts` поверх `Depends(auth_dep)` (AuthCtx из Phase 1).
- Замена `_onboarding_sessions` in-memory dict на persistent таблицу с TTL.
- Замена `subprocess.run(['docker','restart','telegram-listener'])` на periodic
  reconcile-loop внутри listener'а.
- Новый background task `ContactCheckWorker` в API-контейнере (как QueueWorker /
  WarmupWorker), обновляет `contacts.tg_status` через checker'а.

**Не в скоупе (для последующих фаз):**
- Агенты (AI-шаблоны) workspace-level — Phase 3.
- Campaign-модель, sender lock per кампания, расписание per кампания — Phase 4.
- Inbox, аналитика, AI-фильтр системных ботов — Phase 5.
- Admin-бот workspace — Phase 6.
- UI для CRUD ProxyPool — отложено (модель уже workspace-scoped из Phase 1,
  само управление пулом достаточно сделать как минимальные API-эндпоинты;
  богатый UI прокси-пула — позже).

</domain>

<decisions>
## Implementation Decisions

### Модель контактов и папок

- **D-01:** Создаём НОВУЮ таблицу `contacts (id UUID PK, workspace_id FK NOT NULL,
  folder_id FK NOT NULL, phone VARCHAR(20), username VARCHAR(50), full_name
  VARCHAR(200), source VARCHAR(100), custom JSONB DEFAULT '{}', tg_status
  VARCHAR(20) NOT NULL DEFAULT 'pending', tg_telegram_id BIGINT NULLABLE,
  tg_username_resolved VARCHAR(50) NULLABLE, tg_error TEXT NULLABLE,
  tg_checked_at TIMESTAMPTZ NULLABLE, created_at, updated_at)`.
  Существующая таблица `contacts_cache` остаётся как есть — это per-sender
  Telegram-resolve-кэш (telegram_id + access_hash, необходим для InputPeerUser
  при отправке) и **другая сущность** от workspace-уровня списка контактов.
  Не объединять и не выпиливать.

- **D-02:** Уникальность контактов: **UNIQUE INDEX `(workspace_id, phone) WHERE
  phone IS NOT NULL`** + **UNIQUE INDEX `(workspace_id, username) WHERE
  username IS NOT NULL`**. Контакт идентифицируется парой workspace + (phone OR
  username). Phone хранится в E.164 (`+79...`).

- **D-03:** Поведение при дубликате во время CSV-импорта — **skip + report**:
  существующий контакт не трогается; в ответе `POST /contacts/import` приходит
  `{imported: N, skipped_duplicates: M, skipped_invalid: K,
  skipped_phones: [...]}`. Никакого "merge" / "update by upload" в v1.

- **D-04:** Каждый контакт принадлежит ровно **одной папке** (FLDR-01).
  `contacts.folder_id` NOT NULL FK. Между папками — операция **move**
  (`POST /contacts/{id}/move` или batch `POST /contacts/move`), а не копия.

- **D-05:** Папки: модель `folders (id UUID PK, workspace_id FK NOT NULL,
  name VARCHAR(100) NOT NULL, created_at, updated_at)` + UNIQUE
  `(workspace_id, name)`. CRUD: создание, переименование, удаление.

- **D-06:** **Удаление папки запрещено** если в ней есть контакты ИЛИ если
  она привязана к активной кампании. API возвращает 409 Conflict с телом
  `{contact_count: N, active_campaigns: [...]}`. Пользователь либо вручную
  очищает / перемещает контакты, либо подтверждает удаление с
  `?force=true` (force каскадно удаляет контакты этой папки). Тема "active
  campaigns" — заглушка на Phase 4: в Phase 2 проверяется только наличие
  контактов; код пишем с TODO-меткой `# TODO(phase-4): also block on active
  campaign attachment`. Никаких системных папок "Inbox" в v1.

### CSV-импорт

- **D-07:** **Двух-шаговый UI-flow с user-defined column mapping**:
  1. `POST /contacts/import/preview` — загружает CSV (multipart),
     парсит первые ~50 строк, возвращает `{columns: [...], sample_rows: [...],
     suggested_mapping: {col_0: 'phone', ...}}`. Suggested mapping —
     эвристика по имени колонки (case-insensitive, английские/русские
     алиасы: phone/телефон, name/имя/full_name, source/источник).
  2. `POST /contacts/import` — принимает JSON `{import_id (от preview),
     folder_id ИЛИ folder_name, mapping: {col_idx: 'phone' | 'username' |
     'full_name' | 'source' | 'custom.<key>', ...}, on_duplicate: 'skip'}`.
     Backend применяет mapping, нормализует phone в E.164, всё что
     замаплено в `custom.<key>` уходит в `contacts.custom` JSONB.
  Файл preview можно держать в `/tmp/{import_id}` с TTL 30 мин (или
  блобе в БД — planner решит).

- **D-08:** Если в `mapping` не указан ни `phone` ни `username` — `422
  Unprocessable Entity` с явным сообщением. Если в строке CSV отсутствует
  и phone и username — строка попадает в `skipped_invalid` с reason.

- **D-09:** `folder_name` (без `folder_id`) — auto-create папки в этом
  workspace если её ещё нет; так же работает push через Workspace API-ключ
  (CONT-03, FLDR-03).

- **D-10:** Push контактов через Workspace API: `POST /api/v1/contacts` —
  принимает либо single, либо batch (до 1000 за запрос); auth — Workspace
  API-ключ из Phase 1 (`X-Workspace-Key`). Тот же engine что и для CSV
  (deduplication + nullable folder_name auto-create). Возврат — структура
  как у CSV-импорта (`imported / skipped_*`).

### Sender lifecycle, статус, rate limits

- **D-11:** **Два независимых поля + derived `status` в API**:
  - `senders.auth_status` (существующее, остаётся): `ok / session_expired /
    session_revoked / banned / deactivated`. Меняется автоматически
    listener'ом при событиях Telethon.
  - `senders.lifecycle_status` (новое поле, SQLEnum): `active / warmup /
    paused`. Меняется явно пользователем через UI.
  - `senders.is_active` (legacy boolean): дропается в миграции 013, заменяется
    на `lifecycle_status`. Где старый код проверял `is_active=True` —
    становится `lifecycle_status='active' AND auth_status='ok'`.
  - API `GET /senders` / `GET /senders/{id}` возвращает derived
    `status: 'active' | 'warmup' | 'paused' | 'error'`:
    если `auth_status != 'ok'` → `'error'`; иначе `lifecycle_status`.
  - В derived ответе также `auth_status` и `lifecycle_status` поля отдельно —
    UI может показать детали в tooltip.

- **D-12:** Lifecycle переходы:
  - Дефолт после онбординга = `active`.
  - Юзер вручную через UI кнопки "Pause" / "Resume" / "Send to warmup".
  - `error` — derived, не storable; листенер только обновляет `auth_status`,
    derived computed на чтении.
  - При `error` (auth_status != ok) queue.py и WarmupWorker пропускают sender'а
    автоматически.

- **D-13:** Rate limits **per-sender** в трёх новых INT-полях с
  `server_default 4 / 20 / 150`:
  - `senders.rate_per_min INT NOT NULL DEFAULT 4`
  - `senders.rate_per_hour INT NOT NULL DEFAULT 20`
  - `senders.rate_per_day INT NOT NULL DEFAULT 150`
  - `services/queue.py` читает из sender'а вместо захардкоженных констант
    (`RATE_LIMIT_PER_MIN` и т.д. — выпиливаются как глобальные).
  - Существующие эмпирические константы 4/20/150 остаются как DB-defaults —
    это и есть "зелёный коридор".

- **D-14:** "Зелёный коридор" = **warn-only**, без hard-block на разумных
  значениях. На API-уровне:
  - Hard cap: **10 / 50 / 300** (за этими цифрами API возвращает `422` с
    сообщением "exceeds maximum safe limit, contact support if you need
    higher"). Это защита от опечатки "1500/hour".
  - Soft cap = green corridor: 4/20/150. Между green и hard cap API
    возвращает `200 OK` с полем `warnings: [{field: 'rate_per_min',
    value: 7, recommended_max: 4, severity: 'warning'}]`. UI рендерит
    предупреждение в форме настроек.

- **D-15:** Старые AIContext-поля рассылки (`max_message_length`,
  `response_delay_seconds`) **не переезжают** на sender в Phase 2 — это
  концерн агента (Phase 3) / кампании (Phase 4). Здесь не трогаем.

### Onboarding state и listener sync

- **D-16:** In-memory `_onboarding_sessions: dict` **выпиливается полностью**.
  Заменяется на новую таблицу:
  ```
  onboarding_sessions(
    id UUID PK,
    workspace_id UUID FK NOT NULL,
    phone VARCHAR(20) NOT NULL,
    phone_code_hash TEXT NOT NULL,
    encrypted_session_string TEXT NOT NULL,  -- зашифровано через encryption.encrypt_session
    role VARCHAR(20) NOT NULL DEFAULT 'sender',  -- 'sender' | 'checker'
    proxy JSONB NULLABLE,
    status VARCHAR(20) NOT NULL,  -- 'code_sent' | 'awaiting_2fa' | 'completed' | 'failed'
    expires_at TIMESTAMPTZ NOT NULL,  -- created_at + 10 min
    created_at TIMESTAMPTZ DEFAULT now()
  )
  ```
  TTL = 10 минут. Periodic cleanup в lifespan: `DELETE FROM onboarding_sessions
  WHERE expires_at < now()` каждые 5 минут.

- **D-17:** Telethon-клиент онбординга всё равно живёт в памяти процесса —
  это ограничение Telethon (объект клиента не сериализуется). Но **state
  для resume после рестарта api-контейнера** (phone_code_hash + encrypted
  session_string между `start` → `verify_code` → `verify_2fa`) — в БД.
  Если в момент verify_code дискнутый dict пуст (after restart), API
  поднимает client из `encrypted_session_string` (decrypt → StringSession)
  и продолжает sign_in. Это надёжный путь даже при рестарте.

- **D-18:** **subprocess.run(['docker','restart','telegram-listener']) —
  выпиливается полностью** (в новых роутерах его нет, в старом senders.py
  тоже не возвращаем). Заменяется на:
  - **Periodic reconcile loop в listener.py**: каждые 30 секунд
    `SELECT id, workspace_id, session_string, proxy, role, auth_status,
    lifecycle_status FROM senders WHERE role='sender' AND
    lifecycle_status='active' AND auth_status='ok'`. Сравнение с
    `currently_connected: dict[sender_id, TelethonClient]`. Diff →
    `connect_new(missing)`, `disconnect(removed_or_paused)`.
  - Никакого LISTEN/NOTIFY, никакого `listener_dirty` флага. Задержка до
    30 секунд между CRUD sender'а и connect — допустима для онбординга
    (юзер всё равно не отправит сообщение в первые секунды).
  - Этот loop — отдельный asyncio task в listener'е (параллельно
    Telethon event-loop'у), запускается при старте контейнера.

### TG-проверка контактов и checker

- **D-19:** **Async pipeline** для проверки контактов в Telegram:
  - `POST /contacts/import` сразу INSERT'ит контакты с `tg_status='pending'`
    и возвращает `202 Accepted` с total/imported/skipped счётчиками.
  - Новый background task `ContactCheckWorker` (по аналогии с QueueWorker)
    запускается в lifespan API-контейнера. Цикл: SELECT contacts с
    `tg_status='pending'` LIMIT N → батчем через checker-аккаунт
    workspace'а (`ResolvePhone` / `ResolveUsername`) → UPDATE
    `tg_status = 'registered' | 'not_registered' | 'error'` +
    `tg_telegram_id`, `tg_username_resolved`, `tg_checked_at`,
    `tg_error`.
  - Worker уважает rate limits checker'а (тоже из `senders.rate_per_*`)
    и FloodWait error (по аналогии с QueueWorker).
  - UI поллит `GET /contacts?folder_id=...` (по обновлению `tg_checked_at`
    видно прогресс) — без SSE / websocket'ов в v1.

- **D-20:** Если в workspace нет sender'а с `role='checker'`:
  - Импорт **проходит**: контакты сохраняются с `tg_status='unchecked'`
    (новый возможный статус помимо pending / registered / not_registered /
    error). `ContactCheckWorker` пропускает такие контакты (нет checker'а
    → нечем проверять).
  - API `GET /workspace` возвращает флаг `has_checker: false` — UI
    рендерит баннер "Add a dedicated checker account to verify
    phone presence in Telegram before sending".
  - Когда checker появляется, новые контакты идут с `pending` (нормальный
    flow); существующие `unchecked` юзер может явно перепроверить
    через `POST /contacts/recheck` (batch endpoint). Auto-recheck при
    появлении checker'а — деферрим (см. deferred).

- **D-21:** **Onboarding checker'а = тот же flow что и sender'а**, с
  toggle `role: 'sender' | 'checker'` на экране подтверждения SMS-кода.
  Один набор API-эндпоинтов `POST /onboarding/start`,
  `POST /onboarding/verify-code`, `POST /onboarding/verify-2fa`,
  `POST /onboarding/qr-start`, `POST /onboarding/qr-finish`. `role`
  передаётся в `verify-code` (как уже сделано в существующем коде) и
  пишется в `senders.role` при создании. `listener.py` reconcile-loop
  фильтрует `WHERE role='sender'` — checker'ы туда не попадают.

### Workspace proxy pool — минимальный API

- **D-22:** ProxyPool уже workspace-scoped (Phase 1 D-03). В Phase 2
  добавляем минимальный CRUD: `GET /workspace/proxies`,
  `POST /workspace/proxies`, `DELETE /workspace/proxies/{id}`,
  `POST /senders/{id}/assign-proxy {proxy_id}`. UI богатого "Proxy pool
  management" deferred — достаточно списка и кнопок add/delete/assign.

### Claude's Discretion

- **C-01:** Имя миграции `013_phase2.sql` или раздробить (013/014/015) — planner
  решит, что чище. Идемпотентность (`IF NOT EXISTS`) обязательна (CLAUDE.md).
- **C-02:** Хранение CSV preview между preview-запросом и import-запросом —
  `/tmp/{import_id}` (с cleanup-task) ИЛИ блобом в `csv_imports` таблице.
  Planner решит после оценки размера файлов.
- **C-03:** Точная структура response `warnings[]` (см. D-14) — planner подберёт
  shape под FastAPI/Lovable конвенцию (массив объектов или dict со списками).
- **C-04:** Точные имена endpoint'ов и pydantic схем — planner выберет под
  существующие конвенции (`schemas/__init__.py` PascalCase, имена `ContactCreate`,
  `ContactImportRequest`, `FolderResponse` и т.п.).
- **C-05:** Wave 0 расширение pytest-конфигурации Phase 1 (D-17) под Phase 2
  фикстуры (workspace + sender + checker фикстуры) — planner добавит в Wave 0
  своей структуры.
- **C-06:** Интервал periodic reconcile loop'а — 30 секунд по дефолту (D-18), но
  planner может настроить (env var с дефолтом).

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project-level
- `CLAUDE.md` — главные правила: запрет Alembic, raw SQL migrations 012_+, async
  everywhere, общение на русском, не трогать rate-limit интервалы без обсуждения
- `.planning/PROJECT.md` — Key Decisions (Campaign первичная сущность, agent
  отвязан от sender'а, rate limits per-sender, расписание per-campaign)
- `.planning/REQUIREMENTS.md` §Phase 2 — ровно те 17 требований (ONBD-01..05,
  SNDR-01..03, CONT-01..05, FLDR-01..03), что должен закрыть Phase 2
- `.planning/ROADMAP.md` §"Phase 2: TG Accounts & Contacts" — Success Criteria
  и состав плана (5 планов: onboarding wiring, sender settings, folders,
  contacts/CSV, contact check)

### Phase 1 контекст (must read)
- `.planning/phases/01-workspace-foundation/01-CONTEXT.md` — все D-01..D-18
  Phase 1, особенно D-04 (изоляция через `.where(workspace_id == ctx.workspace_id)`),
  D-11..D-13 (AuthDep / AuthCtx, dual auth JWT+API-key), D-03 (ProxyPool BYO,
  workspace-scoped), D-14 (старые роутеры выпилены из main.py)
- `.planning/phases/01-workspace-foundation/01-CONTEXT.md` §"Deferred Ideas → Для
  Phase 2" — UI proxy upload, рерайт senders.py/onboarding.py, замена
  subprocess.run docker restart

### Codebase intel
- `.planning/codebase/ARCHITECTURE.md` — слойная разбивка router→service→data,
  паттерн QueueWorker (background asyncio task в lifespan) — служит шаблоном для
  нового `ContactCheckWorker`
- `.planning/codebase/STRUCTURE.md` — где должны жить новые роутеры
  (`app/routers/contacts.py`, `app/routers/folders.py`,
  `app/routers/check_contacts.py`), миграции в `migrations/013_*.sql`
- `.planning/codebase/INTEGRATIONS.md` — Telethon abstraction, encryption,
  proxy_tuple builder в `app/services/telegram.py`
- `.planning/codebase/CONCERNS.md` — `_onboarding_sessions` in-memory dict,
  `subprocess.run(['docker','restart'])`, `Sender.role String(20)` без CHECK
  (новые enum-поля должны быть SQLEnum)

### Существующий код (читать перед изменением)
- `app/routers/onboarding.py` — текущий flow start/verify-code/verify-2fa/qr,
  использует `verify_api_key` (удалён в Phase 1, переписываем под AuthDep) и
  `_onboarding_sessions: dict` (заменяется на onboarding_sessions таблицу)
- `app/routers/senders.py` — CRUD sender'а, `subprocess.run(['docker','restart'])`
  в `_restart_listener` (выпиливается), использует `verify_api_key`
- `app/routers/check_contacts.py` — существующий resolve через checker, шаблон
  для `ContactCheckWorker`
- `app/services/listener.py` — точка где добавляется periodic reconcile loop
- `app/services/queue.py` — паттерн background asyncio task в lifespan +
  rate-limit логика; шаблон для `ContactCheckWorker`. Текущие constants
  `RATE_LIMIT_PER_MIN/HOUR/DAY` — переезжают на sender-уровень
- `app/services/telegram.py` — `make_telegram_client`, `build_proxy_tuple` —
  переиспользуем для onboarding и checker'а
- `app/services/encryption.py` — `encrypt_session` / `decrypt_session` для
  `onboarding_sessions.encrypted_session_string`
- `app/models/__init__.py` — `Sender` (расширяется), `ContactCache` (НЕ
  объединяется с новой `contacts`), `ProxyPool` (workspace-scoped из Phase 1)
- `migrations/012_workspace.sql` — паттерн раздельных миграций после Phase 1,
  следующая 013_

### Telethon / Telegram (внешний)
- Telethon docs §contacts.ResolvePhone / §contacts.ResolveUsername — операции
  которые делает checker (см. существующий `check_contacts.py`)
- Telegram MTProto rate-limit notes — почему 4/20/150 это "зелёный коридор"
  (CLAUDE.md и PROJECT.md фиксируют это эмпирически, источник — внутренний опыт
  AGS Foods)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- **AuthDep / AuthCtx** из Phase 1 (`app/utils/auth.py`) — все новые роутеры
  `Depends(auth_dep)`, фильтр `where(Model.workspace_id == ctx.workspace_id)`
- **QueueWorker** в `app/services/queue.py` — шаблон background asyncio task с
  rate-limit / FloodWait / retry. `ContactCheckWorker` строится по тому же паттерну
- **WarmupWorker** в `app/services/warmup.py` — второй пример background task,
  с явным lifecycle (start/stop в lifespan)
- **make_telegram_client / build_proxy_tuple** в `app/services/telegram.py` —
  использовать для onboarding и checker'а; не дублировать логику
- **encryption.encrypt_session / decrypt_session** — единственный способ
  работы с session_string'ами, в т.ч. в новой `onboarding_sessions`
- **secrets.token_urlsafe** + `bcrypt.hashpw` — уже использованы для
  Workspace API-ключей (Phase 1 D-13), не нужно ничего нового для auth
- **ContactCache** существует как per-sender Telegram-resolve-кэш — переиспользуется
  для отправки в Phase 4; не путать с новой `contacts`
- **Phase 1 pytest-инфраструктура** (D-17) — `conftest.py` с `async_db_session`,
  `valid_supabase_jwt`, `async_client` фикстурами — расширяется в Phase 2 (см. C-05)

### Established Patterns
- Все таблицы: UUID PK, `workspace_id UUID NOT NULL` FK CASCADE, `server_default`
  для timestamp'ов; новые enum-поля → `SQLEnum(...)`, не `String(20)`
- Миграции — raw SQL, идемпотентные, нумерация 013_+
- `AsyncSession` через `Depends(get_db)`; never `time.sleep`, `requests`, `print`
- Background workers — singleton instances в module (`worker = ContactCheckWorker()`)
  + start/stop в FastAPI lifespan
- Pydantic v2 (`model_config = ConfigDict(...)`)
- API endpoints под `/api/v1/<resource>`, response через Pydantic schemas из
  `app/schemas/__init__.py`

### Integration Points
- **`app/main.py`** — регистрировать новые роутеры `onboarding`, `senders`,
  `contacts`, `folders`, `check_contacts` (после Phase 1 они выпилены —
  возвращаем их workspace-scoped версии). Запускать `ContactCheckWorker` в lifespan
- **`app/services/listener.py`** — добавить periodic reconcile loop как
  параллельный asyncio task
- **`app/services/queue.py`** — выпилить захардкоженные `RATE_LIMIT_*` constants,
  читать из `sender.rate_per_min/hour/day`
- **`docker-compose.yml`** — рестарт обоих api/listener; никакого
  `docker.sock` mount'а больше не требуется (был для `subprocess.run`)
- **`app/schemas/__init__.py`** — новые модели `ContactCreate`,
  `ContactImportRequest`, `ContactImportPreviewResponse`, `FolderResponse`,
  `SenderUpdate` (расширенный с rate_per_* и lifecycle_status), `OnboardingStart`
  и т.д.

### Anti-patterns, которые НЕ повторять (из CONCERNS.md и Phase 1)
- НЕ хранить никакой in-flight state в `_onboarding_sessions: dict` (D-16)
- НЕ `subprocess.run(['docker', 'restart', ...])` в роутерах (D-18)
- НЕ `Sender.role = Column(String(20))` без CHECK — новые enum-поля через
  `SQLEnum` (применимо к `senders.lifecycle_status`, `contacts.tg_status`,
  `onboarding_sessions.status`)
- НЕ CORS `allow_origins=['*']` — оставляем как есть для outreach-platform
  (Phase 1 рекомендация: ограничить до Lovable-домена через env), но это
  не блокер Phase 2
- НЕ дублировать workspace-isolation: ВСЕ запросы к новым таблицам должны
  иметь `.where(... .workspace_id == ctx.workspace_id)` (или helper); leave
  TODO-комментарии `# TODO(v2-rls): replaced by RLS policy app.workspace_id`
  как в Phase 1

</code_context>

<specifics>
## Specific Ideas

- **БД чистая (D-01 Phase 1 подтверждён юзером в discussion)**: миграции
  Phase 2 не должны делать backfill / тащить данные. Просто создать новые
  таблицы и колонки, в том числе с агрессивными constraint'ами (UNIQUE по
  phone в workspace) — данных, которые могли бы их нарушить, не существует.
- **Phone format = E.164**: при импорте нормализовать (`+79001234567`),
  отвергать без leading `+` или с не-цифрами. Эту нормализацию делать
  один раз на write — потом везде сравнения по нормализованной форме.
- **Workspace API-ключ для push контактов**: точно тот же AuthDep, что и
  для UI — клиент в n8n шлёт `X-Workspace-Key: wsk_...`. Никакого
  отдельного auth для n8n flow.
- **`role` в senders.py остаётся String(20) — но добавляется CHECK constraint
  в миграции 013** (`CHECK role IN ('sender', 'checker')`). Migrating
  String→SQLEnum в Phase 2 — деферрим (см. deferred), но CHECK дёшево добавить.
- **listener reconcile loop фильтрует `WHERE auth_status='ok' AND
  lifecycle_status='active' AND role='sender'`** — checker'ы и paused/warmup
  senders туда не идут (их Telethon-клиент держать не нужно).
- **rate_per_* поля можно отдавать на API как nested object**: `{rate_limits:
  {per_minute: 4, per_hour: 20, per_day: 150}, warnings: [...]}` — пара
  copy-paste-friendly для Lovable. Точная shape — C-03.

</specifics>

<deferred>
## Deferred Ideas

### Для Phase 3 (Agents)
- Перевод `senders.role` с `String(20)+CHECK` на полноценный `SQLEnum` —
  совместим с Phase 3 рерайтом моделей под agent-as-template
- `AIContext` поля `max_message_length`, `response_delay_seconds` —
  переезд на campaign/agent (как там планируется в Phase 3/4)

### Для Phase 4 (Campaigns)
- "Active campaign" блокировка удаления папки (D-06) — сейчас TODO-метка
- Lock sender'а в кампанию (один active campaign per sender) — модель
  кампании ещё не существует, в Phase 2 не реализуем
- "Папка как target кампании" + досыпание (CAMP-09) — Phase 4
- Переменные `{{имя}}, {{username}}, {{custom.X}}` в тексте сообщения —
  Phase 4 (subst на этапе постановки в очередь, не при импорте)

### Для Phase 5 / 6
- AI-фильтр системных ботов (SpamBot и др.) на listener'е — Phase 5
- Admin-бот для уведомлений об ошибках sender'а (auth_status != ok) —
  Phase 6 (метаданные уже будут)

### Для v2
- Auto-recheck `unchecked` контактов когда workspace впервые добавляет
  checker'а (см. D-20). Сейчас — ручной endpoint `POST /contacts/recheck`.
- Богатый UI ProxyPool management (D-22): группы прокси, rotation
  политики, healthcheck прокси. В v1 — минимальный CRUD.
- SSE / WebSocket прогресс импорта (D-19 альтернатива): сейчас UI поллит
  `GET /contacts`. SSE — когда появится первый клиент с CSV на 10k+ контактов.
- Контакт во многих папках (D-04 переосмысление) — в v2 если у клиентов
  возникнет реальная нужда. Сейчас "one folder per contact" с move-операцией.

### Tech debt, обнаруженный во время обсуждения
- `senders.is_active` дропается в миграции 013 (заменяется на `lifecycle_status`)
  — нужно прочесать кодовую базу на использования `is_active` до миграции (planner
  включит в plan 02-02). Сейчас grep даёт его в `routers/senders.py`,
  `services/queue.py` фильтрах, `services/listener.py` startup-фильтре.
- `app/database.py` `Base.metadata.create_all` (Phase 1 C-04) — всё ещё нерешён.
  Если planner Phase 2 сочтёт уместным — фиксит здесь. Иначе деферрит дальше.

</deferred>

---

*Phase: 02-tg-accounts-contacts*
*Context gathered: 2026-05-21*
