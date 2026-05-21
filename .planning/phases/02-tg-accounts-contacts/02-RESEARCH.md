# Phase 2: TG Accounts & Contacts — Research

**Researched:** 2026-05-21
**Domain:** Telethon-онбординг + persistent state, CSV-импорт, async pipeline проверки контактов в TG, periodic-reconcile listener
**Confidence:** HIGH (по реальному коду проекта) / MEDIUM (по Telethon-паттернам, проверка по training data + коду)

## Summary

Phase 2 ставит на фундамент Phase 1 (AuthDep / AuthCtx, workspace_id на всех таблицах, миграция 012) пять предметных функций: workspace-scoped онбординг Telegram-аккаунтов, per-sender настройки (rate limits + lifecycle + proxy), модель контактов с папками, двух-шаговый CSV-импорт, асинхронная проверка контактов через checker-аккаунт. CONTEXT.md `02-CONTEXT.md` уже задаёт 22 решения (D-01..D-22) — research ниже **не дублирует** эти решения, а отвечает на **gap'ы**, которые planner будет вынужден закрыть для написания планов 02-01..02-05.

Главное, что нашлось при чтении реального кода: **после Phase 1 девять старых роутеров (`onboarding.py`, `senders.py`, `check_contacts.py`, `send.py`, `queue.py`-router, `conversations.py`, `contexts.py`, `warmup.py`, `proxy_pool.py`) физически остались в `app/routers/`, но все они импортируют удалённый `from app.routers.auth import verify_api_key` — этот импорт сломан, файлы НЕ ВКЛЮЧЕНЫ в `app/main.py`**. То есть план 02-01 (онбординг) и 02-02 (sender settings) — это переписывание этих файлов поверх `Depends(auth_dep)` (Phase 1 D-11/D-12), с теми же endpoint'ами, но с workspace_id во всех запросах. Старая бизнес-логика (`_onboarding_sessions: dict`, `subprocess.run(["docker","restart"])`, `is_active` boolean, AGS-специфика) — выпиливается в этом же действии.

**Primary recommendation:** Wave-структура из 5 планов разворачивается в 4 волны (Wave 0 = миграция 013 + расширение pytest-конфы → Wave 1 = модели + папки + sender-settings параллельно → Wave 2 = онбординг-рерайт + listener reconcile-loop параллельно → Wave 3 = ContactCheckWorker + CSV-import/push с общим `import_service` модулем). Каждый план в Wave 1+ держится исключительно на миграции из Wave 0; внутри Wave 1 ни один из трёх планов не блокирует другой; в Wave 2 онбординг блокирует listener.reconcile (тот должен фильтровать по новому полю `lifecycle_status`).

## Project Constraints (from CLAUDE.md)

**Из `/Users/andrewbruce/Documents/outreach-platform/CLAUDE.md`** — планер обязан соблюдать ВСЕ директивы как locked decisions:

- **Async везде**: только `async def` + `AsyncSession`. Никогда `time.sleep()`, синхронный `requests`, `print()` вместо `logging`. Phase 2: `ContactCheckWorker` пишем в async-pattern QueueWorker; CSV-парсинг через `csv.DictReader` (стандартная либа, sync — но в одном-shot read запросе допустимо; для больших файлов читать в threadpool через `asyncio.to_thread`).
- **Миграции — только raw SQL** в `migrations/`, нумерация `013_+`, идемпотентность (`IF NOT EXISTS` / `IF EXISTS`). Никогда Alembic. Phase 2: миграция `013_phase2.sql` (или разбивка — C-01) добавляет таблицы `contacts`, `folders`, `onboarding_sessions` + поля `senders.rate_per_min/hour/day`, `senders.lifecycle_status`; дропает `senders.is_active` (D-11).
- **Безопасность**: сессии зашифрованы (Fernet через `app/services/encryption.py`), API_KEY не в логах. Phase 2: `onboarding_sessions.encrypted_session_string` обязательно через `encrypt_session()` перед записью; в логах только `session_id[:8]` и `phone[:6]***`.
- **Очередь и rate-limit интервалы**: не трогать без явного обсуждения. Phase 2: эмпирические константы 4/20/150 переезжают на `senders.rate_per_*` как defaults (D-13); сами значения **сохраняются**.
- **Retry-логика FloodWait**: не ломать. Phase 2: `ContactCheckWorker` использует тот же паттерн `await asyncio.sleep(e.seconds)` + ранний выход батча (см. `app/services/checker.py:211-219` — уже корректно реализовано в `CheckerService`, переиспользуем).
- **Общение со мной русский, код / коммиты — английский**. Phase 2: имена endpoint'ов / схем / таблиц на английском (CONTEXT.md `<specifics>`); комментарии в коде допустимы как RU, так и EN (в существующем коде смешано — оба допустимы); docstring и log-message — английский предпочтителен, но проект уже имеет много RU log-message (`logger.info(f"✅ Авторизация успешна...")`), не ломаем стиль.

## User Constraints (from CONTEXT.md)

### Locked Decisions

**Модель контактов и папок:**

- **D-01:** Новая таблица `contacts(id UUID PK, workspace_id FK NOT NULL, folder_id FK NOT NULL, phone VARCHAR(20), username VARCHAR(50), full_name VARCHAR(200), source VARCHAR(100), custom JSONB DEFAULT '{}', tg_status VARCHAR(20) NOT NULL DEFAULT 'pending', tg_telegram_id BIGINT NULLABLE, tg_username_resolved VARCHAR(50) NULLABLE, tg_error TEXT NULLABLE, tg_checked_at TIMESTAMPTZ NULLABLE, created_at, updated_at)`. Существующая `contacts_cache` НЕ ТРОГАЕТСЯ — это другая сущность (per-sender resolve cache).
- **D-02:** Уникальность контактов: `UNIQUE INDEX (workspace_id, phone) WHERE phone IS NOT NULL` + `UNIQUE INDEX (workspace_id, username) WHERE username IS NOT NULL`. Phone в E.164.
- **D-03:** При дубликате в CSV-импорте — **skip + report** (`imported / skipped_duplicates / skipped_invalid`). Никаких merge / update.
- **D-04:** Контакт принадлежит **одной** папке (FLDR-01). `contacts.folder_id NOT NULL FK`. Между папками — операция **move**.
- **D-05:** Папки `folders(id UUID PK, workspace_id FK NOT NULL, name VARCHAR(100) NOT NULL, created_at, updated_at)` + UNIQUE `(workspace_id, name)`. CRUD: создание / переименование / удаление.
- **D-06:** **Удаление папки запрещено**, если в ней есть контакты ИЛИ если она привязана к активной кампании. 409 Conflict с `{contact_count, active_campaigns: []}`. С `?force=true` — каскадное удаление контактов. Active-campaigns — TODO(phase-4); сейчас проверяется только `contact_count`. Никаких системных папок "Inbox".

**CSV-импорт:**

- **D-07:** Двух-шаговый flow `POST /contacts/import/preview` (multipart, парсит первые ~50 строк) → `POST /contacts/import` (JSON с `import_id` + `folder_id|folder_name` + `mapping`). Mapping значения: `phone | username | full_name | source | custom.<key>`. Suggested mapping — эвристика по имени колонки (англ/рус алиасы).
- **D-08:** Если в `mapping` нет ни `phone`, ни `username` → 422. Если в строке нет ни phone, ни username → строка в `skipped_invalid` с reason.
- **D-09:** `folder_name` без `folder_id` → auto-create в этом workspace. Работает и для CSV, и для Workspace API push.
- **D-10:** Push контактов через Workspace API: `POST /api/v1/contacts` — single или batch (до 1000). Auth — `X-Workspace-Key` (Phase 1 D-13). Тот же engine что и CSV (dedup + folder_name auto-create).

**Sender lifecycle, статус, rate limits:**

- **D-11:** Два независимых поля + derived `status` в API:
  - `senders.auth_status` (существующее, оставляем): `ok / session_expired / session_revoked / banned / deactivated` — меняется listener'ом автоматически.
  - `senders.lifecycle_status` (новое, **SQLEnum**): `active / warmup / paused` — меняется юзером.
  - `senders.is_active` (legacy boolean) — **дропается** в миграции 013.
  - В API derived `status: 'active' | 'warmup' | 'paused' | 'error'` (если `auth_status != 'ok'` → `'error'`, иначе `lifecycle_status`). В ответе также есть `auth_status` и `lifecycle_status` отдельно.
- **D-12:** Lifecycle переходы: default после онбординга = `active`. UI: Pause / Resume / Send to warmup. При `auth_status != ok` queue.py и WarmupWorker пропускают sender автоматически.
- **D-13:** Rate limits per-sender, 3 новых INT-поля с server_default `4 / 20 / 150`. `services/queue.py` читает из sender, глобальные `MAX_MSGS_PER_*` константы — **выпиливаются**.
- **D-14:** "Зелёный коридор" = warn-only. Hard cap = **10 / 50 / 300** (за ним 422). Между green и hard — 200 OK + `warnings: [{field, value, recommended_max, severity: 'warning'}]`.
- **D-15:** `AIContext.max_message_length` / `response_delay_seconds` НЕ переезжают на sender в Phase 2 — это Phase 3 / Phase 4.

**Onboarding state и listener sync:**

- **D-16:** In-memory `_onboarding_sessions: dict` **полностью выпиливается**. Replaced на таблицу `onboarding_sessions(id UUID PK, workspace_id FK, phone VARCHAR(20), phone_code_hash TEXT, encrypted_session_string TEXT, role VARCHAR(20) DEFAULT 'sender', proxy JSONB NULLABLE, status VARCHAR(20), expires_at TIMESTAMPTZ, created_at)`. TTL = 10 мин. Periodic cleanup в lifespan: `DELETE FROM onboarding_sessions WHERE expires_at < now()` каждые 5 минут.
- **D-17:** Telethon-клиент онбординга всё равно живёт в памяти процесса (объект не сериализуется). Но **state для resume после рестарта api-контейнера** (phone_code_hash + encrypted session_string между start → verify_code → verify_2fa) — в БД. Если в момент verify_code dict пуст (after restart) → поднимаем client из `encrypted_session_string` (decrypt → StringSession) и продолжаем sign_in.
- **D-18:** `subprocess.run(['docker','restart','telegram-listener'])` **выпиливается полностью**. Replaced на periodic reconcile-loop в listener.py каждые 30 сек: `SELECT id, workspace_id, session_string, proxy, role, auth_status, lifecycle_status FROM senders WHERE role='sender' AND lifecycle_status='active' AND auth_status='ok'` → diff с `currently_connected: dict[sender_id, TelethonClient]` → connect_new / disconnect_removed.

**TG-проверка контактов и checker:**

- **D-19:** Async pipeline: `POST /contacts/import` возвращает `202 Accepted` с total/imported/skipped. Контакты сохраняются с `tg_status='pending'`. `ContactCheckWorker` (background asyncio task в lifespan API) выбирает pending → батч через checker → UPDATE `tg_status = registered | not_registered | error` + `tg_telegram_id`, `tg_checked_at`, `tg_error`. Worker уважает rate limits checker'а (`senders.rate_per_*`) и FloodWait. UI поллит `GET /contacts?folder_id=...`.
- **D-20:** Если в workspace нет sender'а с `role='checker'` → контакты импортятся с `tg_status='unchecked'` (новый статус). `ContactCheckWorker` пропускает. `GET /workspace` отдаёт `has_checker: false`. Когда checker появляется — новые контакты идут с `pending`; существующие `unchecked` юзер может ручно перепроверить через `POST /contacts/recheck` (batch). Auto-recheck — деферрим.
- **D-21:** Onboarding checker'а = тот же flow что и sender'а, с toggle `role: 'sender' | 'checker'` на экране подтверждения SMS-кода. Один набор endpoint'ов `POST /onboarding/{start, verify-code, verify-2fa, qr-start, qr-finish}`. `role` передаётся в `verify-code`. Listener фильтрует `WHERE role='sender'` — checker'ы туда не попадают.

**Workspace proxy pool — минимальный API:**

- **D-22:** Минимальный CRUD: `GET /workspace/proxies`, `POST /workspace/proxies`, `DELETE /workspace/proxies/{id}`, `POST /senders/{id}/assign-proxy {proxy_id}`. UI богатого management — deferred.

### Claude's Discretion

- **C-01:** Имя миграции `013_phase2.sql` или разбивка (013/014/015) — planner решит.
- **C-02:** Хранение CSV preview между preview-запросом и import-запросом — `/tmp/{import_id}` (с cleanup-task) ИЛИ блобом в `csv_imports` таблице.
- **C-03:** Точная shape `warnings[]` (D-14).
- **C-04:** Точные имена endpoint'ов и pydantic схем.
- **C-05:** Wave 0 расширение pytest-конфигурации Phase 1 (D-17) — Phase 2 фикстуры (workspace + sender + checker фикстуры).
- **C-06:** Интервал periodic reconcile-loop — 30 сек по дефолту (D-18); planner может вынести в env var.

### Deferred Ideas (OUT OF SCOPE)

**Для Phase 3:** Перевод `senders.role` с `String(20)+CHECK` на полный `SQLEnum`; перенос `AIContext.max_message_length / response_delay_seconds` на campaign/agent.

**Для Phase 4:** "Active campaign" блокировка удаления папки (D-06 TODO); lock sender'а в кампанию; "папка как target кампании" + досыпание; подстановка переменных `{{имя}}, {{username}}, {{custom.X}}` (в Phase 4 при постановке в очередь, НЕ при импорте).

**Для Phase 5/6:** AI-фильтр системных ботов (Phase 5); admin-бот для уведомлений об ошибках sender (Phase 6).

**Для v2:** Auto-recheck `unchecked` контактов при добавлении checker; богатый UI proxy pool; SSE / WebSocket прогресс импорта; контакт во многих папках (D-04 переосмысление).

**Tech debt (упомянут в discussion):** Прочесать кодовую базу на использования `is_active` до миграции 013 (см. ниже §"Hidden Dependencies"); `app/database.py` `Base.metadata.create_all` (Phase 1 C-04) — решение оставлено за planner Phase 2 (фиксит или деферрит дальше).

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| ONBD-01 | Пользователь добавляет TG-аккаунт через телефон + SMS-код | §"Telethon onboarding flow" — `client.send_code_request(phone)` → `client.sign_in(phone, code, phone_code_hash)`. Persistent state в `onboarding_sessions` table. |
| ONBD-02 | Поддерживается 2FA | `SessionPasswordNeededError` → `client.sign_in(password=...)`. Already implemented в `app/routers/onboarding.py:537-547` — переносим логику в новый router. |
| ONBD-03 | Поддерживается QR-вход | `client.qr_login()` + `qr_login.wait(timeout=120)` — existing в `app/routers/onboarding.py:650-708`. State (qr_login object) НЕ сериализуем — деферрим QR на in-process (fallback на SMS если контейнер перезапущен в момент wait). |
| ONBD-04 | Аккаунт привязан к workspace | `senders.workspace_id` уже добавлен Phase 1 миграцией 012. `POST /senders` берёт `workspace_id` из AuthCtx. |
| ONBD-05 | Список аккаунтов со статусом | `GET /senders` фильтрует `where(Sender.workspace_id == ctx.workspace_id)` (AuthDep), возвращает derived `status` (D-11). |
| SNDR-01 | Per-account rate limits с warning | Миграция 013 добавляет 3 INT-поля. API возвращает `warnings[]` (D-14). `queue.py` читает из sender. |
| SNDR-02 | Per-account proxy / выбор из пула | `senders.proxy JSONB` уже есть. D-22: добавляем `POST /senders/{id}/assign-proxy`. |
| SNDR-03 | Статус аккаунта | D-11 derived `status`. Lifecycle переходы — D-12. |
| CONT-01 | CSV-импорт | D-07 двух-шаговый flow. |
| CONT-02 | Контакты привязаны к workspace | `contacts.workspace_id NOT NULL`. |
| CONT-03 | Push через Workspace API | D-10. |
| CONT-04 | Проверка наличия в TG | D-19 `ContactCheckWorker` + D-20 fallback `unchecked`. |
| CONT-05 | Поля контакта (phone/username/full_name/source/custom JSONB) | D-01. |
| FLDR-01 | Контакты группируются по папкам | D-04. |
| FLDR-02 | CRUD папок | D-05 + D-06 (запрет удаления непустой). |
| FLDR-03 | При импорте выбор папки (создаётся если не существует) | D-09. |

## Real Code State (after Phase 1) — what's actually in the repo

> Этот раздел отвечает на research-вопрос #1 и одновременно фиксирует "no surprises" для planner'а.

### Что Phase 1 уже сделал

| Что | Где | Подтверждение |
|-----|-----|---------------|
| AuthCtx + auth_dep | `app/utils/auth.py` | 246 строк, реализует JWT + API-key branching (D-11 Phase 1) |
| Workspace, UserWorkspace, WorkspaceApiKey модели | `app/models/__init__.py:31-68` | Уже в `Base.metadata` |
| workspace_id FK на 11 tenant-таблицах | `migrations/012_workspace.sql:54-132` | Каждая таблица: `senders, messages_log, contacts_cache, ai_contexts, message_queue, conversations, warmup_pool, warmup_sessions, warmup_messages, proxy_pool, context_contact_assignments` |
| workspace_id в ORM-моделях | `app/models/__init__.py` | Поле есть у каждой tenant-модели |
| tests/conftest.py с фикстурами | `tests/conftest.py` | `_setup_database` (autouse, session-scope; применяет `Base.metadata.create_all` + 012 миграцию через `exec_driver_sql`), `async_db_session`, `async_client`, `valid_supabase_jwt(sub, email, exp, aud)`, `expired_supabase_jwt` |
| pytest + pytest-asyncio в requirements | `requirements.txt:27-29` | Уже установлено (Phase 1 D-17) |
| email-validator, bcrypt, python-jose, qrcode, PySocks, telethon | `requirements.txt` | Все нужные либы для Phase 2 уже есть |
| `app/main.py` — только `health, workspace` роутеры | `app/main.py:11,64-65` | Все остальные include_router удалены (D-14 Phase 1) |

### Что Phase 1 НЕ сделал (важно для planner)

| Что | Текущее состояние | Что planner делает в Phase 2 |
|-----|-------------------|------------------------------|
| Старые роутеры `onboarding/senders/check_contacts/queue-router/send/contexts/conversations/warmup/proxy_pool` | Файлы физически на месте в `app/routers/`, **но импортируют удалённый `from app.routers.auth import verify_api_key`** (см. список в "Hidden Dependencies" ниже). При попытке include_router → `ModuleNotFoundError`. | План 02-01 переписывает `onboarding.py` под AuthDep+workspace; план 02-02 переписывает `senders.py`; план 02-05 переписывает `check_contacts.py`. План 02-03/04 пишут новые `folders.py` / `contacts.py`. Прочие старые файлы (`send, queue-router, contexts, conversations, warmup-router, proxy_pool`) **остаются за рамками Phase 2** — планер их в include_router не возвращает (proxy_pool частично — см. D-22). |
| `app/routers/auth.py` | **Не существует** (удалён Phase 1) | НЕ возвращать; новый AuthDep лежит в `app/utils/auth.py`. |
| `subprocess.run(['docker','restart','telegram-listener'])` | Жив в трёх местах: `app/routers/senders.py:36-50` (`_restart_listener` функция), `app/routers/senders.py:148,221` (вызовы из `create_sender` и `update_sender`), `app/routers/onboarding.py:210-215` (`_auto_save_reauth` хелпер для реавторизации) | План 02-01 (onboarding rewrite) и 02-02 (sender rewrite) выпиливают ВСЕ три места. План reconcile-loop (часть 02-01 либо 02-02 — см. wave organization ниже) добавляет periodic reconcile в listener.py. |
| `_onboarding_sessions: dict` | Жив в `app/routers/onboarding.py:46`, активно используется в 12 endpoint'ах (start, verify-code, verify-2fa, qr/start, qr/status, cancel, reauth/{slug}, reauth/qr/{slug}, _wait_for_qr фоновая task, _auto_save_reauth) | План 02-01 заменяет на `onboarding_sessions` table + in-process dict только для `TelegramClient` объекта (D-17 явно говорит: Telethon client не сериализуется → объект остаётся в памяти, но state восстанавливается из БД при рестарте). |
| Глобальные rate-limit константы | `MAX_MSGS_PER_MINUTE/HOUR/DAY = 4/20/150` в `app/services/queue.py:42-44` | План 02-02 (sender settings) меняет на чтение из `sender.rate_per_*`. |
| `Sender.is_active` boolean | `app/models/__init__.py:84`. Используется в: `routers/senders.py` (5 мест), `routers/health.py:37`, `routers/send.py` (3 проверки), `routers/warmup.py` (3 места), `routers/check_contacts.py:90`, `services/listener.py:331,474,1003,1061,1066` (UPDATE при auth-error + фильтр в `get_active_senders`), `services/rotation.py:48,147`, `services/warmup.py:171`. | План 02-02 (sender settings): миграция 013 дропает колонку; ВСЕ места заменяются на `lifecycle_status='active' AND auth_status='ok'`. Включая `services/listener.py:148-152` `_set_auth_status` (там тоже `is_active = false`) — но т.к. колонки нет, эту строку нужно убрать. |
| AGS-специфичный default prompt в `ai_engine.py:30-41` | Phase 1 CONCERNS.md помечает как tech-debt | НЕ в скоупе Phase 2 (это agent-сфера → Phase 3). |
| Phase 1 tests (`test_auth_dep.py`, `test_workspace_router.py`, `test_workspace_api_keys.py`, `test_migration_012.py`) | Работают, conftest.py с фикстурами стабилен | Wave 0 Phase 2 расширяет conftest.py: добавляет фабрики `make_workspace`, `make_sender`, `make_checker`, `make_folder`, `make_contact` — см. §"Validation Architecture" ниже. |

### Файлы которых ЕЩЁ нет (создаются в Phase 2)

```
app/routers/folders.py            # план 02-03
app/routers/contacts.py            # план 02-04
app/routers/onboarding.py          # план 02-01 (рерайт, файл будет с тем же путём)
app/routers/senders.py             # план 02-02 (рерайт)
app/routers/check_contacts.py      # план 02-05 (рерайт под нового workera + recheck endpoint)
app/services/contact_check_worker.py  # план 02-05 (новый async worker)
app/services/csv_import.py         # план 02-04 (CSV-парсер, dedup, mapping)
app/services/onboarding_state.py   # план 02-01 (helpers для onboarding_sessions table)
migrations/013_*.sql               # Wave 0 (или 013-015 — C-01)
```

## Telethon Onboarding Flow — конкретные паттерны

> Research-вопрос #2: persistent state recovery через `encrypted_session_string`.

### Существующий flow (`app/routers/onboarding.py`)

Phase 2 переиспользует, **не переписывая Telethon-вызовы**, только обёртывая в AuthDep + persistent state:

```python
# 1. POST /onboarding/start  (плюс: запись в onboarding_sessions с status='code_sent')
client = make_telegram_client(StringSession(), proxy=proxy_dict)
await client.connect()
sent_code = await client.send_code_request(phone)
# sent_code.phone_code_hash — обязателен для следующего шага
# session_string = client.session.save()  ← это пустая сессия, но Telethon
#                                            attach'ит к ней DC-routing и auth_key из send_code_request
# WRITE: onboarding_sessions(workspace_id=ctx.workspace_id, phone, phone_code_hash=sent_code.phone_code_hash,
#                            encrypted_session_string=encrypt_session(session_string),
#                            role='sender', proxy=proxy_dict, status='code_sent',
#                            expires_at=now()+10min)
# Telethon client остаётся в in-process dict (D-17), keyed by onboarding_session.id

# 2. POST /onboarding/verify-code  (тот же session_id из шага 1)
# RECOVERY PATH (if in-process dict пуст после рестарта api-контейнера):
#   row = SELECT * FROM onboarding_sessions WHERE id = :sid
#   client = make_telegram_client(StringSession(decrypt_session(row.encrypted_session_string)),
#                                 proxy=row.proxy)
#   await client.connect()
# HOT PATH (dict has client): client = _in_process_clients[session_id]
try:
    await client.sign_in(phone=row.phone, code=request.code, phone_code_hash=row.phone_code_hash)
    # success → encrypt session, INSERT в senders, UPDATE onboarding_sessions.status='completed'
except SessionPasswordNeededError:
    # 2FA → UPDATE onboarding_sessions.status='awaiting_2fa'
    #       также сохранить новый client.session.save() (он мог обновиться после sign_in attempt)
    return {"status": "2fa_required"}
except PhoneCodeInvalidError:  # → 400 PHONE_CODE_INVALID
except PhoneCodeExpiredError:  # → 400 PHONE_CODE_EXPIRED + UPDATE status='failed'
except FloodWaitError as e:    # → 429 retry_after=e.seconds

# 3. POST /onboarding/verify-2fa
# Recovery: то же что в шаге 2 — поднимаем client из encrypted_session_string
try:
    await client.sign_in(password=request.password)
except PasswordHashInvalidError:  # → 400
except FloodWaitError:            # → 429
```

**Что важно:** `client.session.save()` после `send_code_request` дёт строку, которая **уже несёт DC-routing** (Telegram перенаправляет к нужному data center). Без этого после рестарта `send_code_request` придётся вызывать заново (новый `phone_code_hash`), и юзер должен повторно ввести код. Текущий код в `app/routers/onboarding.py` НЕ сохраняет session string на шаге 1 — это нужно добавить именно для resume-flow (D-17).

**FloodWait на verify_code:** Типичные значения 60–300 секунд после нескольких неверных попыток; на `start_onboarding` может прилететь FloodWait в **часах** при злоупотреблении одного номера (это уже обрабатывается в existing code как HTTP 429 retry_after).

**QR flow (`qr_login`):**

```python
# POST /onboarding/qr-start
client = make_telegram_client(StringSession(), proxy=proxy_dict)
await client.connect()
qr_login = await client.qr_login()        # возвращает QRLogin object, имеет .url и .wait()
qr_image = _make_qr_image(qr_login.url)    # base64 PNG
# QR-объект НЕ сохраняем в БД — он завязан на client (внутри держит auth_request).
# Запускаем background task qr_login.wait(timeout=120). После рестарта API
# контейнера wait умирает — юзер должен начать заново. Принимаем это ограничение
# (QR-flow обычно 30-60 секунд, риск рестарта в этом окне низкий).
```

**Recovery для QR — деферрим:** persistent QR-state потребовал бы сохранять `auth_token_id` из QR-логина, что приватный API Telethon. В CONTEXT.md этот вопрос явно не закрыт — рекомендация: QR-only-in-process, при рестарте 404 SESSION_NOT_FOUND + UI начинает заново.

## Periodic Reconcile Loop (listener.py)

> Research-вопрос #3 + #8.

### Структура

`app/services/listener.py` уже сегодня стартует все Telethon-клиенты в `run()` (line 1086-1100): `tasks = [self.start_client(s) for s in senders]; await asyncio.gather(*tasks)`. Каждый `start_client` — бесконечный цикл с auto-reconnect. После Phase 2 этот startup-flow сохраняется, но **добавляется параллельный reconcile-task**.

```python
# В TelegramListener.__init__ — добавить:
self.reconcile_interval = int(os.environ.get("LISTENER_RECONCILE_INTERVAL", "30"))
self._reconcile_task: Optional[asyncio.Task] = None
self._stop_event = asyncio.Event()  # для graceful shutdown

# В run() после первоначального gather:
async def run(self):
    ...
    initial_senders = await self.get_active_senders()  # уже фильтрует role='sender' AND is_active=true → меняем на lifecycle_status='active' AND auth_status='ok'
    
    # Начальное подключение
    for s in initial_senders:
        asyncio.create_task(self.start_client(s))  # не gather — он блокирует
    
    # Параллельный reconcile-loop
    self._reconcile_task = asyncio.create_task(self._reconcile_loop())
    
    # Ждём signal-handler'а (см. ниже)
    await self._stop_event.wait()

async def _reconcile_loop(self):
    while self.running:
        try:
            await asyncio.sleep(self.reconcile_interval)
            if not self.running:
                break
            
            desired = {s["id"]: s for s in await self.get_active_senders()}
            current = set(self.clients.keys())  # current — это slug'и, в desired — id; нужна консистентность
            
            # ПЕРВЫЙ ВЫЗОВ ПОСЛЕ ДОБАВЛЕНИЯ:
            new_ids = set(desired.keys()) - current
            for sid in new_ids:
                logger.info(f"🔄 [reconcile] Подключаем нового sender'а: {desired[sid]['slug']}")
                asyncio.create_task(self.start_client(desired[sid]))
            
            # УДАЛЁННЫЙ ИЛИ ПЕРЕВЕДЁННЫЙ В PAUSED/WARMUP:
            removed_ids = current - set(desired.keys())
            for sid in removed_ids:
                slug = self._slug_by_id(sid)
                logger.info(f"🔄 [reconcile] Отключаем sender'а: {slug}")
                client = self.clients.pop(slug, None)
                if client and client.is_connected():
                    await client.disconnect()
            
            # ИЗМЕНЕНИЕ PROXY (D-13 hint):
            # Сравниваем desired[sid]['proxy'] с тем, с чем sender был подключён.
            # Если изменился → disconnect + reconnect с новым proxy.
            # Это требует хранить snapshot proxy в `self._proxy_snapshot: dict[sender_id, dict]`
            # — обновлять при connect, сравнивать в reconcile.
            
            logger.debug(f"🔄 [reconcile] tick: +{len(new_ids)} -{len(removed_ids)}, total={len(self.clients)}")
        except Exception as e:
            logger.error(f"❌ [reconcile] error: {e}", exc_info=True)

# В TelegramListener.stop() — расширить:
async def stop(self):
    self.running = False
    self._stop_event.set()
    if self._reconcile_task and not self._reconcile_task.done():
        self._reconcile_task.cancel()
        try:
            await self._reconcile_task
        except asyncio.CancelledError:
            pass
    # ...existing client.disconnect() loop
```

**Ключи реконсиляции:**

- **id vs slug**: Текущий `self.clients: dict[str, TelegramClient]` keyed by **slug** (`app/services/listener.py:1018`). Reconcile сравнивает по `sender_id` (UUID) — потому что `slug` юзер может переименовать (UPDATE senders SET slug). Рекомендация: добавить mirror `self.clients_by_id: dict[str, TelegramClient]` либо переключить основной dict на id. Planner решит — минимум: добавить snapshot `self._connected_sender_ids: set[str]` и помечать при connect/disconnect.
- **Graceful SIGTERM**: `asyncio.Event` (`self._stop_event`) — стандартный паттерн. Signal-handler ставит event → main loop выходит → cancel reconcile_task → disconnect всех clients. Текущий `signal_handler` в `app/services/listener.py:1117-1119` уже создаёт task через `listener.stop()` — расширить (см. выше). Telethon `client.disconnect()` сам по себе async и graceful.

### Proxy change handling (research-вопрос #8)

Telethon **не handles** proxy change на лету. Если `senders.proxy` изменился через `POST /senders/{id}/assign-proxy`, реконсилятор должен:

1. Обнаружить разницу (`desired.proxy != self._proxy_snapshot[sid]`).
2. `client.disconnect()`.
3. Создать новый client с новым proxy (через `make_telegram_client(StringSession(session_string), proxy=new_proxy)`).
4. `client.connect()`.
5. Re-register event handlers (`@client.on(events.NewMessage(incoming=True))` — текущий код в `start_client` это делает на каждый retry, можно положиться).
6. Обновить `self.clients[slug] = new_client` + `self._proxy_snapshot[sid] = new_proxy`.

Проще всего: **обращаться с proxy-change как с reconnect через start_client** — пометить sender как "to-reconnect", удалить из dict, добавить в new_ids на следующем tick'е.

**Лог-сообщения для reconcile** (по конвенциям существующего кода с эмодзи):

```python
logger.info(f"🔄 [reconcile] tick: connected={N}, desired={M}, +{added} -{removed}")
logger.info(f"🔄 [reconcile] connecting sender={slug} (workspace={ws[:8]}, role={role})")
logger.info(f"🔄 [reconcile] disconnecting sender={slug} (reason: lifecycle_status={status} or auth_status={auth})")
logger.warning(f"🔄 [reconcile] proxy changed for sender={slug}, will reconnect on next tick")
```

## CSV Import Storage — `/tmp` vs DB blob

> Research-вопрос #4 + C-02.

**Реальные размеры в v1:** Целевой клиент = SaaS с CSV `phone, name, source, ...` на 1–10 тыс. строк. Один контакт со средними полями ~80 байт → 10 000 строк ≈ **800 KB**. С BOM и кавычками ~1 MB max realistic.

**Вариант A: `/tmp/{import_id}/raw.csv` + TTL cleanup task**

- Плюсы: zero DB overhead, простая отладка (можно `ls /tmp/`), естественная связь preview→import через путь.
- Минусы:
  - `/tmp` в контейнере не persistent — рестарт api-контейнера убивает file. Юзер, который сделал preview 5 минут назад, при import получит 404.
  - В Docker Compose не shared между API-инстансами (если когда-нибудь масштабируем → 2 api-контейнера). В v1 один контейнер — не проблема.
  - Нужен cleanup-task (`asyncio.create_task(periodic_tmp_cleanup())` в lifespan) — удалять файлы старше TTL.
- TTL: 30 минут (D-07 уже предлагает).

**Вариант B: блоб в таблице `csv_imports(id UUID PK, workspace_id FK, file_data BYTEA NOT NULL, columns JSONB, sample_rows JSONB, suggested_mapping JSONB, created_at, expires_at)`**

- Плюсы: persistent (рестарт не убивает), workspace-scoped автоматически (auth + filter), atomic cleanup (`DELETE WHERE expires_at < now()`), легко тестировать (нет filesystem).
- Минусы: 800 KB BYTEA на каждый preview — это OK для Postgres (TOAST автоматом сжимает), но если юзер делает preview несколько раз — таблица растёт. Cleanup решает.
- BYTEA быстрее писать/читать (small text → no TOAST лимит).

**Рекомендация (для planner):** **Вариант B (БД-блоб)** — для v1 SaaS-продукта, где предсказуемость превыше микро-оптимизаций. Сделать миграцию `013_*` с таблицей `csv_imports(file_data BYTEA NOT NULL, expires_at TIMESTAMPTZ NOT NULL DEFAULT now()+'30 minutes')` + index `(expires_at)`. Cleanup в том же `onboarding_sessions` cleanup-task'е (один общий task для двух TTL-таблиц).

Если planner предпочтёт `/tmp` — это тоже OK для v1, но в плане 02-04 должна быть явная задача "cleanup-task в lifespan", иначе риск disk-leak.

## Phone Normalization — E.164

> Research-вопрос #5.

**Что есть в проекте:**

- `phonenumbers` пакет НЕ в `requirements.txt`. Только `email-validator==2.1.0` (для email-валидации в Pydantic, не для phone).
- Существующая нормализация в коде — наивная regex/strip:
  - `app/routers/onboarding.py:246-248` — `phone.strip().replace(" ", "").replace("-", "")` + добавление `+` если отсутствует.
  - `app/routers/check_contacts.py:26` — `PHONE_RE = re.compile(r"^\+\d{7,15}$")` — только валидация формата, не нормализация.
  - Аналогично в `app/routers/onboarding.py:398-401` для start.

**Edge cases для российских номеров (90% v1-клиентов из СНГ):**

- `+79001234567` — каноническая E.164 форма для RU.
- `89001234567` — пользователь набирает с 8 (legacy RU format). Нужно: убрать leading 8 → префикс `+7`.
- `79001234567` — без `+`. Нужно: добавить `+`.
- `+7 (900) 123-45-67` — с форматированием. Нужно: strip non-digits, preserve leading `+`.
- `8 (900) 123-45-67` — нужно: strip non-digits → `89001234567` → `+79001234567`.

**Международные edge cases:**

- Казахстан: `+77`, начинается так же как `+7` (Россия) — после strip leading `8` (как RU) можно случайно сломать казахский номер. Поэтому правило "leading 8 → +7" применять **только для 11-digit строк** где после strip leading 8 идёт 10 цифр.
- Беларусь: `+375`, обычно вводится правильно.
- Украина: `+380`.

**Рекомендация для planner:** **НЕ тащить `phonenumbers`** (тяжёлая либа, 5+ MB данных). Реализовать чистый regex-нормализатор в `app/utils/phone.py`:

```python
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
    # RU heuristic: only for 11-digit strings starting with 8
    if not had_plus and len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    e164 = "+" + digits
    if not re.match(r"^\+\d{7,15}$", e164):
        return None
    return e164
```

**Тесты этого хелпера** обязательны (Wave 0 fixture): покрытие — `+79001234567`, `89001234567`, `79001234567`, `+7 (900) 123-45-67`, `+380501234567`, `abc` (None), `""` (None), `+1234` (None — too short), `+1234567890123456` (None — too long).

## ContactCheckWorker — стратегия rate-limit и параллельность

> Research-вопрос #6.

### Reuse existing CheckerService

`app/services/checker.py` уже **реализует** именно то, что нужно `ContactCheckWorker`'у на уровне одной батч-операции:

- `check_phones(checker_id, checker_slug, encrypted_session, phones, proxy)` — один lock per checker_slug (`asyncio.Lock`), батч с FloodWait handling, partial result при FloodWait, polite delay `random.uniform(2.0, 3.5)` между вызовами ResolvePhone (line 207-209).
- Кеширует в `contacts_cache` (cross-sender lookup).

**Phase 2 ContactCheckWorker = тонкая обёртка над CheckerService:**

```python
class ContactCheckWorker:
    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self.batch_size = 10   # phones per checker call — см. ниже
        self.poll_interval = 5  # seconds — sleep between DB polls
    
    async def _tick(self):
        async with AsyncSessionLocal() as db:
            # SELECT первых N pending контактов любого workspace, который имеет checker
            rows = await db.execute(text("""
                SELECT c.id, c.workspace_id, c.phone, c.username, s.id as checker_id, s.slug,
                       s.session_string, s.proxy, s.rate_per_min, s.rate_per_hour, s.rate_per_day
                FROM contacts c
                JOIN senders s ON s.workspace_id = c.workspace_id
                    AND s.role = 'checker' AND s.auth_status = 'ok'
                WHERE c.tg_status = 'pending'
                ORDER BY c.created_at ASC
                LIMIT :n
            """), {"n": self.batch_size})
            # group by checker_id — один checker может проверять несколько контактов из своего workspace
        
        for checker_id, group in groupby(rows, key=lambda r: r.checker_id):
            phones = [r.phone for r in group if r.phone]
            # USERNAME path: контакты с username, но без phone — отдельный flow ResolveUsername
            #   (можно объединить, но planner решит — separate code path проще)
            summary = await checker_service.check_phones(
                checker_id=str(checker_id),
                checker_slug=checker_slug,
                encrypted_session=session_string,
                phones=phones,
                proxy=proxy,
            )
            # Обновить contacts.tg_status в зависимости от summary.results
            await self._apply_results(group, summary)
        
        await asyncio.sleep(self.poll_interval)
```

### Batch size

Существующий `app/routers/check_contacts.py:32-37` ограничивает `phones: List[str]` `max_length=20` — это ручной endpoint. Для фонового worker'а:

- **Slow but safe: batch=1** — каждые 5 сек одна phone-resolve. 12 phones/min. При workspace на 1000 контактов = 83 минуты. Долго, но FloodWait риск минимален.
- **Compromise: batch=10** — каждые 5 сек ~10 phones (с polite delay 2-3 сек внутри батча → батч идёт ~25 сек, потом 5 сек idle). 24 phones/min. Workspace на 1000 контактов = 42 минуты.
- **Aggressive: batch=20** — старый endpoint лимит. ~50 phones/min с polite delay. **Высокий риск FloodWait** для checker'а.

**Рекомендация:** `batch_size = 5`, `poll_interval = 5`. Между resolved phones внутри `CheckerService.check_phones` polite delay 2-3.5 сек (уже реализовано). Получаем ~30 phones/min на одного checker'а — безопасно и достаточно быстро для UX.

**Per-checker rate-limit:** D-19 говорит "Worker уважает rate limits checker'а (тоже из `senders.rate_per_*`)". То есть после `rate_per_minute` resolved phones — стоп на минуту. Для batch=5 это не блокирует (5 < 4 — но 4 это `rate_per_min` default). Нужно: считать число completed phones за последнюю минуту/час/день per-checker (можно из `contacts_cache.updated_at` или из нового счётчика `senders.checker_msgs_*` — последнее сложнее, проще считать из cache). Planner подумает.

### Параллельность с QueueWorker

Существующий `QueueWorker` обрабатывает per-sender очередь сообщений. `ContactCheckWorker` обрабатывает per-checker resolve. **Они ИЗОЛИРОВАНЫ**:

- QueueWorker фильтрует `senders.role='sender'` (через `services.queue` — там нет явного фильтра, но `send.py` ставит в очередь только sender'ов, а checker'ы не имеют clients в listener — см. listener filter `role='sender'`).
- ContactCheckWorker фильтрует `senders.role='checker'`.

**Один и тот же sender НЕ может быть и checker, и sender одновременно** (`role` exclusive). Поэтому нет общих ресурсов на уровне Telethon-клиента. Lock в `CheckerService._locks[checker_slug]` гарантирует, что один checker не делает два параллельных resolve.

**Опасность:** Если в одном workspace будут два checker'а одновременно → ContactCheckWorker должен выбирать round-robin или least-recently-used (через `senders.last_used_at`). В v1 ожидание = один checker per workspace; planner может оставить `LIMIT 1` checker per query до тех пор пока не появится business case.

**Coordination через DB advisory lock** — НЕ требуется в v1 (single api-container). Если когда-нибудь 2+ api-инстанса будут параллельно держать ContactCheckWorker → нужен Postgres advisory lock `pg_try_advisory_lock(checker_id_hashed)`. Сейчас деферрим.

## SQLEnum vs String + CHECK

> Research-вопрос #7.

### Существующий precedent в проекте

- `MessageQueue.status = Column(SQLEnum(QueueItemStatus), ...)` (`app/models/__init__.py:175`) — полноценный Postgres ENUM-тип через SQLAlchemy. В миграции 012 нет CREATE TYPE — этим занимается `Base.metadata.create_all` (SQLAlchemy сама создаёт ENUM на init).
- `MessageLog.message_type = Column(SQLEnum(MessageType), ...)` — то же.
- `UserWorkspace.role = Column(String(20), ...)` + CHECK constraint в SQL (`user_workspaces_role_check CHECK (role IN ('owner', 'admin', 'member'))`) (`migrations/012_workspace.sql:24-26`). **Это образец Phase 1.**
- `Sender.role = Column(String(20), ...)` — БЕЗ CHECK (concerns.md flags это как tech-debt).

### Trade-offs

**Postgres ENUM (CREATE TYPE):**

- Плюсы: атомарная типизация, error-message Postgres'а понятнее (`invalid input value for enum...`).
- Минусы: **сложно расширять**. `ALTER TYPE ADD VALUE 'new_value'` нельзя выполнить в транзакции (особенность Postgres ≤ 12) → нарушает idempotent migration pattern проекта. Phase 3 точно будет расширять `senders.lifecycle_status` (если появится новый статус) — это сложно.
- В Phase 1 в миграции 012 ENUM-типы НЕ создавались как Postgres types — `Base.metadata.create_all` отвечает за их создание. Это противоречит "raw SQL migrations only" — `init_db()` запускается на пустой БД (Phase 1 C-04 tech-debt).

**String + CHECK constraint:**

- Плюсы: расширяется обычным `ALTER TABLE ... DROP CONSTRAINT ... ADD CONSTRAINT ...` (атомарно, idempotent). Совместимо с rest of project pattern (12-я миграция использует это для `user_workspaces.role`).
- Минусы: ORM-уровневая проверка отсутствует — нужно валидировать в Pydantic-схеме отдельно.

**Рекомендация для planner:** **String + CHECK constraint** для новых полей Phase 2:

- `senders.lifecycle_status VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (lifecycle_status IN ('active', 'warmup', 'paused'))`
- `contacts.tg_status VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (tg_status IN ('pending', 'registered', 'not_registered', 'error', 'unchecked'))`
- `onboarding_sessions.status VARCHAR(20) NOT NULL CHECK (status IN ('code_sent', 'awaiting_2fa', 'completed', 'failed'))`

В ORM-моделях: `Column(String(20), ...)` + Pydantic-валидация через `Literal[...]` в схемах. Это и быстрее (не нужно CREATE TYPE), и проще расширять, и консистентно с `UserWorkspace.role` из Phase 1.

В Phase 3, когда будем переписывать `senders.role` (deferred), planner Phase 3 может одновременно конвертировать `lifecycle_status` в полноценный SQLEnum — или оставить как есть.

## pytest Fixtures для Phase 2

> Research-вопрос #9.

### Уже есть (Phase 1 D-17, `tests/conftest.py`)

- `_setup_database` — session-scope autouse, создаёт схему через `Base.metadata.create_all` + применяет миграцию 012 через `exec_driver_sql`. Drop в teardown.
- `async_db_session` — function-scope, изолированная DB session с rollback.
- `async_client` — `httpx.AsyncClient` с ASGITransport (in-process, нет реальной сети).
- `valid_supabase_jwt(sub, email, exp, aud)` — фабрика валидных HS256 JWT.
- `expired_supabase_jwt` — истёкший JWT.

### Что добавить в Phase 2 (Wave 0)

Расширение `tests/conftest.py` (или новый `tests/fixtures.py`, импортируемый из conftest):

```python
@pytest_asyncio.fixture
async def test_workspace(async_db_session) -> Workspace:
    """Создаёт workspace для теста."""
    ws = Workspace(name="Test Workspace")
    async_db_session.add(ws)
    await async_db_session.commit()
    return ws

@pytest_asyncio.fixture
async def auth_ctx(test_workspace) -> AuthCtx:
    """AuthCtx с тестовым workspace."""
    return AuthCtx(
        workspace_id=test_workspace.id,
        user_id="test-user-1",
        source="jwt",
        role="owner",
    )

@pytest_asyncio.fixture
async def test_sender_factory(async_db_session, test_workspace):
    """Фабрика sender'ов для теста.
    
    Usage:
        sender = await test_sender_factory(slug="acc1", lifecycle_status="active",
                                            rate_per_min=4, rate_per_hour=20, rate_per_day=150)
    """
    counter = {"n": 0}
    async def _make(**overrides):
        counter["n"] += 1
        n = counter["n"]
        defaults = dict(
            workspace_id=test_workspace.id,
            slug=f"test-sender-{n}",
            name=f"Test Sender {n}",
            phone=f"+7900000{n:04d}",
            session_string="encrypted_stub",
            role="sender",
            auth_status="ok",
            lifecycle_status="active",
            rate_per_min=4,
            rate_per_hour=20,
            rate_per_day=150,
        )
        defaults.update(overrides)
        s = Sender(**defaults)
        async_db_session.add(s)
        await async_db_session.commit()
        return s
    return _make

@pytest_asyncio.fixture
async def test_checker(test_sender_factory):
    """Checker-аккаунт для теста."""
    return await test_sender_factory(role="checker", slug="test-checker")

@pytest_asyncio.fixture
async def test_folder(async_db_session, test_workspace):
    """Папка контактов для теста."""
    f = Folder(workspace_id=test_workspace.id, name="Test Folder")
    async_db_session.add(f)
    await async_db_session.commit()
    return f

@pytest_asyncio.fixture
async def test_contacts_factory(async_db_session, test_workspace, test_folder):
    """Фабрика контактов с настраиваемыми полями."""
    async def _make(count=1, tg_status="pending", **overrides):
        contacts = []
        for i in range(count):
            c = Contact(
                workspace_id=test_workspace.id,
                folder_id=test_folder.id,
                phone=f"+7901000{i:04d}",
                full_name=f"Contact {i}",
                source="test",
                tg_status=tg_status,
                **overrides
            )
            async_db_session.add(c)
            contacts.append(c)
        await async_db_session.commit()
        return contacts if count > 1 else contacts[0]
    return _make

@pytest_asyncio.fixture
def auth_headers(test_workspace, valid_supabase_jwt):
    """Готовые HTTP-заголовки с JWT для test_workspace."""
    # NOTE: JWT не несёт workspace_id — он создаётся через auth_dep при первом запросе.
    # Но в тестах нам нужен фиксированный workspace — используем user_workspaces row через
    # фабрику (см. test_auth_dep.py pattern).
    token = valid_supabase_jwt(sub=f"test-user-{test_workspace.id}")
    return {"Authorization": f"Bearer {token}"}
```

**Размещение:** В `tests/conftest.py` (т.к. Phase 1 уже там — единый стиль). Если файл становится > 200 строк — разделить на `tests/fixtures/db.py` (модели/фабрики) и `tests/fixtures/auth.py` (auth helpers), реэкспортировать из conftest.

**Wave 0 fixtures-задача:** один план Wave 0, который:

1. Применяет миграцию 013 (см. C-01) к test-БД (расширение `_setup_database`).
2. Добавляет `Workspace`, `Sender`, `Folder`, `Contact`, `OnboardingSession` фабрики.
3. Добавляет helper `make_auth_headers_for(workspace)` — пишет row в `user_workspaces` + возвращает JWT.

## CSV Import Pitfalls

> Research-вопрос #10.

### Lib choice

**Не нужен `pandas`** — overkill для v1 (3+ MB деп, slow startup, requires `numpy`). Стандартная `csv.DictReader` достаточна:

- Поддерживает quoted fields, custom delimiter, dialect detection (`csv.Sniffer`).
- Streaming-чтение (не грузит весь файл в память).
- ~50 строк code для preview + import.

### Pitfalls и mitigations

1. **BOM в начале файла** (Excel сохраняет CSV с UTF-8 BOM = `\xef\xbb\xbf`).
   - Mitigation: открыть с `encoding="utf-8-sig"` (Python автоматически strip BOM).
2. **Delimiters: `,` vs `;` vs `\t`**.
   - Russian Excel часто экспортирует с `;` (Locale-dependent).
   - Mitigation: `csv.Sniffer().sniff(sample, delimiters=',;\t|')` на первых 1024 байтах. Если не сработал — default `,`.
3. **Encoding**:
   - UTF-8 — каноническая. Excel "Save As CSV UTF-8" сохраняет с BOM.
   - CP1251 — legacy Russian (Excel "Save As CSV (MS-DOS)" или старые Windows).
   - Mitigation: попытка `utf-8-sig` → если `UnicodeDecodeError` → `cp1251` → если опять — 422 `INVALID_ENCODING`. Использовать `chardet`? Нет, лишняя деп. Достаточно two-step fallback.
4. **Кавычки**:
   - Поля содержат `,` или `;` → должны быть в `"..."`.
   - Поля содержат `"` → `""` внутри `"..."`.
   - `csv.DictReader` обрабатывает обе ситуации стандартно (default `quoting=csv.QUOTE_MINIMAL`).
5. **Heading row missing**:
   - `csv.DictReader` берёт первую строку как заголовки. Если у юзера CSV без заголовков — preview покажет данные как заголовки (bad UX).
   - Mitigation: в preview response добавить флаг `looks_like_no_header` — эвристика "все первые ячейки выглядят как телефоны". UI спросит юзера.
6. **Trailing whitespace / empty rows**:
   - Strip values при чтении. Empty rows (`[None, None, ...]`) skip.
7. **Дублирующиеся заголовки**:
   - `csv.DictReader` overwrites при одинаковых ключах. Mitigation: в preview ругаться `422 DUPLICATE_HEADERS`.
8. **Large file DoS**:
   - В v1 ставим soft-limit ~5 MB на upload (FastAPI `Request.body()` уже ограничен по умолчанию).
9. **CSV injection (formula injection)** — `=cmd|...` в CSV: атака на пользователей, открывающих экспортированный CSV в Excel. В v1 это импорт (не экспорт), угрозы нет. Деферрим.

### Skeleton

```python
# app/services/csv_import.py
import csv
import io
from typing import BinaryIO

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

def _heuristic_no_header(headers: list[str]) -> bool:
    """If first row 'looks like' phone numbers, suspect no header."""
    import re
    PHONE_LIKE = re.compile(r"^[+\d][\d\s()-]{6,}$")
    return all(PHONE_LIKE.match(h) for h in headers if h)

def suggest_mapping(columns: list[str]) -> dict:
    """Heuristic: column name → contact field."""
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

## Workspace API Key + JWT через AuthDep

> Research-вопрос #11.

Уже разобрано в `app/utils/auth.py` (Phase 1):

- `auth_dep` (line 51) принимает оба заголовка `Authorization: Bearer ...` и `X-Workspace-Key: wsk_...`.
- Возвращает один и тот же `AuthCtx(workspace_id, user_id, source, role)` — для JWT `user_id` = supabase sub, для API-key `user_id` = None.
- `workspace_id` всегда заполнен (для API-key из таблицы `workspace_api_keys`, для JWT из `user_workspaces` lookup или autocreate).

**Для Phase 2 plan'ера** ничего нового делать НЕ нужно — `Depends(auth_dep)` уже работает для обоих режимов. Push контактов через `POST /api/v1/contacts` с `X-Workspace-Key: wsk_...` — это просто стандартный endpoint с тем же AuthDep.

**Что нужно проверить planner'ом** в плане 02-04:

```python
@router.post("")
async def push_contacts(
    request: ContactPushRequest,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    # ctx.workspace_id — есть и для JWT, и для API-key
    # ctx.source — "jwt" | "api_key" — можно использовать для аудит-лога если нужно
    # ctx.user_id — None для API-key — OK
    ...
```

**Опасности:**

- НЕ ассумить, что `ctx.user_id is not None` — это будет ошибкой для API-key path.
- НЕ забыть `where(Contact.workspace_id == ctx.workspace_id)` в SELECT/UPDATE/DELETE — иначе изоляция нарушена.

## Wave Organization

> Research-вопрос #12.

### Dependency graph (по реальному коду)

```
                ┌──────────────────────────────────────────┐
                │ Wave 0: Migration 013 + pytest fixtures  │
                │ (создаёт contacts, folders,              │
                │  onboarding_sessions tables;             │
                │  расширяет senders с rate_per_*,         │
                │  lifecycle_status; дропает is_active)    │
                └─────────┬────────────────────────────────┘
                          │ блокирует ВСЁ ниже
        ┌─────────────────┼─────────────────────┐
        │                 │                     │
        ▼                 ▼                     ▼
┌─────────────┐  ┌──────────────────┐  ┌──────────────────┐
│ Wave 1a:    │  │ Wave 1b:         │  │ Wave 1c:         │
│ Folders     │  │ Sender Settings  │  │ Contact Model    │
│ (план 02-03)│  │ (план 02-02      │  │ + Push API       │
│             │  │ часть A:         │  │ (часть плана     │
│ - folders   │  │ rate_per_* +     │  │ 02-04: ORM       │
│   ORM model │  │ lifecycle_status │  │ Contact, базовый │
│ - CRUD      │  │ + queue.py       │  │ POST /contacts   │
│   routes    │  │ рерайт констант  │  │ для push;        │
│ - delete    │  │ + senders.py     │  │ CSV-импорт       │
│   conflict  │  │ роутер CRUD)     │  │ — Wave 3)        │
│   handling  │  │                  │  │                  │
└─────────────┘  └──────────────────┘  └──────────────────┘
        │                 │                     │
        └────────┬────────┴─────────────────────┘
                 │
                 ▼
        ┌─────────────────────────────────────────┐
        │ Wave 2a: Onboarding Rewrite (план 02-01)│
        │ - onboarding_sessions table integration │
        │ - subprocess.run() выпиливание (3 места)│
        │ - listener reconcile-loop (часть 02-01) │
        │ - reauth/{slug} flow поверх AuthCtx     │
        │                                         │
        │ Wave 2b: SHOULD BE PART OF 02-01,       │
        │ NOT PARALLEL — listener.reconcile_loop  │
        │ читает sender.lifecycle_status (1b)     │
        │ — но 1b в Wave 1, completed до Wave 2.  │
        │ Параллельность 2a со 2b невозможна, т.к.│
        │ оба меняют listener.py — конфликт.      │
        └─────────────────────┬───────────────────┘
                              │
                              ▼
        ┌────────────────────────────────────────┐
        │ Wave 3: ContactCheckWorker (план 02-05)│
        │ + CSV Import (план 02-04, часть B)     │
        │                                        │
        │ - 02-05 нужно: contacts.tg_status      │
        │   field (есть в Wave 0), checker роль  │
        │   через listener filter (Wave 2a)      │
        │ - 02-04 CSV: нужен contacts table      │
        │   (Wave 0) + folders (Wave 1a)         │
        │                                        │
        │ Эти два плана могут идти параллельно:  │
        │ 02-04 пишет route + csv_import.py,     │
        │ 02-05 пишет contact_check_worker.py +  │
        │ recheck endpoint                       │
        └────────────────────────────────────────┘
```

### Recommended Wave structure

| Wave | Plans | Why parallel / sequential |
|------|-------|---------------------------|
| **Wave 0** | Migration 013 + pytest fixtures расширение (один план; C-01 разрешает разбивку, но для одной волны проще один план) | Блокирует всё — ORM-модели Wave 1+ нужны. |
| **Wave 1** | 02-03 Folders, 02-02 Sender settings rewrite (включая queue.py constants выпиливание и senders.py роутер), 02-04 Contact model + push API skeleton (без CSV) | Параллельно: три независимых router-файла, никаких file-collision'ов. Sender settings меняет queue.py (но queue.py роутер выпилен в Phase 1, остаётся `app/services/queue.py` — модуль worker'а, конфликт только если 02-03/02-04 туда лезут, чего не должно быть). |
| **Wave 2** | 02-01 Onboarding rewrite + listener reconcile-loop (один план — нельзя разделять, т.к. onboarding выпиливает `subprocess.run(['docker','restart'])`, а заменитель = reconcile-loop в том же файле listener.py) | **НЕТ ПАРАЛЛЕЛИ В WAVE 2**. Только 02-01. Можно сократить до одной волны. |
| **Wave 3** | 02-04 CSV import (часть B: preview + import endpoints, csv_import.py service), 02-05 ContactCheckWorker + recheck endpoint | Параллельно: разные сервисные файлы (`csv_import.py` vs `contact_check_worker.py`); общая точка интеграции = `contacts` table (есть с Wave 0). |

**Итого: 4 волны, 5 планов, оптимальная параллельность.** Wave 1 экономит больше всего времени (3 плана разом).

### Гипотезы CONTEXT.md уточнённые

- CONTEXT.md gave hypothesis "Wave 0 миграция (один план или раздробить — C-01)". **Рекомендация: один план в Wave 0** (один SQL-файл `013_phase2.sql` для атомарной транзакции). Разбивка усложняет — нужен порядок применения 013_a, 013_b, 013_c, и `IF NOT EXISTS` идемпотентность не страхует от частичного применения.
- "Wave 2: onboarding rewrite + reconcile loop в listener (параллельно)" — **скорректировано: НЕ параллельно**, оба меняют listener.py и senders.py. Один план 02-01 включает оба изменения.

## Hidden Dependencies

> Research-вопрос #14.

### `senders.is_active` боолейн — 14 мест

При дропе колонки в миграции 013 эти места ВСЕ должны быть переписаны на `lifecycle_status='active' AND auth_status='ok'`. Если хоть одно место не переписать — `column does not exist` runtime error.

| Файл | Строка | Контекст | Action |
|------|--------|----------|--------|
| `app/models/__init__.py` | 84 | `is_active = Column(Boolean, default=True, ...)` | Удалить колонку из ORM-модели |
| `app/routers/health.py` | 37 | `active = sum(1 for s in senders if s.is_active)` | Phase 2 не возвращает этот роутер? Он же `health.py` — он есть. Заменить на `s.lifecycle_status == 'active' and s.auth_status == 'ok'`. |
| `app/routers/onboarding.py` | 204 | `sender.is_active = True` в `_auto_save_reauth` | Заменить на `sender.lifecycle_status = 'active'; sender.auth_status = 'ok'`. |
| `app/routers/senders.py` | 24, 91, 195, 196, 308 | `is_active` в response + create + update + spambot-check | План 02-02 переписывает целиком — `is_active` исчезает из API ответа (заменяется `lifecycle_status`). `SenderUpdate.is_active` → `lifecycle_status`. |
| `app/routers/send.py` | 53, 68, 168, 183, 277 | Проверка перед отправкой | Файл вне Phase 2 scope (план 02-* не возвращает send.py в include_router). Но в Phase 4 (queue rewrite) переписать. Если файл компилируется (import broken — `from app.routers.auth import verify_api_key`) — миграция всё равно сломает по-другому. Решение: оставить ImportError как есть (файл не подключён), но в `app/services/queue.py:1` импорт `from app.models import ... Sender` — ORM-class. Тут уже миграция бьёт. **План 02-02 ОБЯЗАН пройтись по `app/services/queue.py` и заменить `s.is_active` → `s.lifecycle_status == 'active' and s.auth_status == 'ok'`. Grep по services/ нужно делать в начале плана 02-02.** |
| `app/routers/warmup.py` | 48, 50, 74, 111, 114, 116, 170, 171, 175, 199, 217 | router для warmup_pool — НЕ в Phase 2 scope | Импорт `from app.routers.auth import verify_api_key` уже сломан — router не подключается. Колонка `warmup_pool.is_active` — это **ДРУГАЯ колонка**, у `WarmupPool` (line 251 модели). НЕ путать с `senders.is_active`. Эту warmup_pool.is_active не трогаем. |
| `app/routers/check_contacts.py` | 90 | `Sender.is_active.is_(True)` для checker'а | План 02-05 переписывает — фильтр становится `Sender.auth_status == 'ok'` (для checker'а lifecycle_status не релевантен — checker всегда "active"). |
| `app/services/listener.py` | 148-152 (UPDATE), 331 (filter `WHERE is_active=true AND role='sender'`), 474 (warmup_pool WHERE — ДРУГОЕ поле, не senders), 1003, 1061, 1066 (UPDATE при auth error) | Listener.py — ключевой файл Phase 2 (план 02-01 reconcile-loop) | План 02-01 переписывает: `_set_auth_status` теряет `is_active = false` (D-12: derived 'error' при `auth_status != ok`), `get_active_senders` фильтр меняется на `lifecycle_status='active' AND auth_status='ok' AND role='sender'`. |
| `app/services/rotation.py` | 48, 147 | Sender rotation для context-based send | НЕ в Phase 2 scope. Файл импортируется queue.py service. `rotation.py:48` — внутри SELECT, идёт через text(). Этот SQL должен быть переписан в плане 02-02 (или 02-04 если planner так решит) на `s.lifecycle_status='active' AND s.auth_status='ok'`. Иначе queue runtime сломается. **HIGH RISK — добавить в plan 02-02 явный checklist item.** |
| `app/services/warmup.py` | 171 | WarmupWorker фильтр sender'ов | План 02-02 переписывает. Filter `s.is_active = true AND s.role = 'sender'` → `s.lifecycle_status='active' AND s.auth_status='ok' AND s.role='sender'`. |

**Резюме:** План 02-02 должен начаться с `grep -rn "is_active" app/services/ app/routers/health.py app/routers/check_contacts.py app/routers/onboarding.py` и зафиксировать checklist всех 14 мест. Каждое — менять явно.

### Глобальные rate-limit константы — 1 место

`app/services/queue.py:42-44`:

```python
MAX_MSGS_PER_MINUTE = 4
MAX_MSGS_PER_HOUR = 20
MAX_MSGS_PER_DAY = 150
```

Использования:

```bash
grep -rn "MAX_MSGS_PER\|RATE_LIMIT_PER" app/ --include="*.py"
```

(Я делал — нашёл только определения в queue.py:42-44, ни одного import снаружи.) Внутри `queue.py` они используются в `_check_rate_limits` (читай функцию полностью при рерайте — она в queue.py, ~225-325 строк). План 02-02 заменяет на чтение `sender.rate_per_min/hour/day` из БД при каждом sender-tick.

### Other constants связанные с rate-limit

```python
MIN_SEND_INTERVAL = 20
MAX_SEND_INTERVAL = 55
SEND_INTERVAL_FATIGUE = 0.5
MAX_NEW_CONTACTS_PER_HOUR = 15
LONG_PAUSE_EVERY_MIN = 12
LONG_PAUSE_EVERY_MAX = 25
LONG_PAUSE_MIN_SECS = 180
LONG_PAUSE_MAX_SECS = 600
FLOOD_HARD_THRESHOLD = 300
WORK_HOUR_START = 9
WORK_HOUR_END = 20
```

**Эти НЕ трогать в Phase 2** (CLAUDE.md явный запрет, CONTEXT.md `<deferred>` помечает рабочие часы Phase 4). Plan 02-02 переписывает ТОЛЬКО `MAX_MSGS_PER_*` константы.

## Harm-ful patterns (после Phase 1)

> Research-вопрос #13.

Подтверждаю, что следующие patterns **физически живут в коде после Phase 1**:

| Pattern | Где (file:line) | Phase 2 action |
|---------|-----------------|----------------|
| `_onboarding_sessions: dict[str, dict] = {}` | `app/routers/onboarding.py:46` | Выпилить, replaced на `onboarding_sessions` table + in-process dict только для `TelegramClient` instances |
| `subprocess.run(["docker", "restart", "telegram-listener"], ...)` | `app/routers/senders.py:36-50, 148, 221`<br>`app/routers/onboarding.py:209-215` | Выпилить полностью. Replaced на periodic reconcile loop. Также убрать `import subprocess` |
| `MAX_MSGS_PER_MINUTE/HOUR/DAY` глобальные | `app/services/queue.py:42-44` | Заменить на чтение из sender |
| `sender.is_active = False` writes | `app/services/listener.py:149, 308 (senders.py), 196 (senders.py)` | Заменить на `sender.lifecycle_status = 'paused'` (юзер-action) или удалить (для auth-error: derived 'error' статус, не storable) |
| Sender.role = String(20) без CHECK | `app/models/__init__.py:85` | НЕ менять в Phase 2 (deferred Phase 3), но добавить CHECK в миграции 013 (CONTEXT.md `<specifics>` явно говорит) |
| AGS-specific default prompt | `app/services/ai_engine.py:30-41` | НЕ в Phase 2 scope (Phase 3) |
| CORS `allow_origins=["*"]` | Phase 1 уже исправил в `app/main.py:54-60` — теперь `settings.cors_origins_list` | NO ACTION |

**Дополнительный pattern — НЕ упомянут в CONTEXT.md, но HARM-FUL:**

- `app/routers/onboarding.py:34, 487, 580, 654, 714, 762` — `_: str = Depends(verify_api_key)`. После рерайта в плане 02-01 это становится `ctx: AuthCtx = Depends(auth_dep)`.
- Аналогично для всех 9 старых роутеров. Но Phase 2 рерайтит только `onboarding.py`, `senders.py`, `check_contacts.py`. Остальные 6 файлов остаются "broken imports" — это OK (deferred Phase 3-4).

## Lovable / Frontend API Contract — что planner должен задизайнить

> Research-вопрос #15.

**Researcher не пишет полную спецификацию** — это работа planner'а в `PLAN.md` каждого из 5 планов. Ниже только список endpoint'ов, которые UI ожидает (выведено из CONTEXT.md + REQUIREMENTS.md). Planner добавляет схемы / коды ошибок / payload-форматы в своих PLAN.md.

### Onboarding (план 02-01)

```
POST   /api/v1/onboarding/start              {phone, role?}           → {session_id, status: 'code_sent'}
POST   /api/v1/onboarding/verify-code        {session_id, code, role} → {status: 'success' | '2fa_required', ...}
POST   /api/v1/onboarding/verify-2fa         {session_id, password}   → {status: 'success', ...}
POST   /api/v1/onboarding/qr-start           {role?}                  → {session_id, qr_image (base64), status: 'pending'}
GET    /api/v1/onboarding/qr-status/{sid}                              → {status, qr_image?, ...}
POST   /api/v1/onboarding/qr-finish          {session_id, password?}  → {status: 'success', ...} (опционально объединить с qr-status)
DELETE /api/v1/onboarding/cancel/{sid}                                 → {status: 'cancelled'}
POST   /api/v1/onboarding/reauth/{sender_id} (использует существующий sender's phone+proxy) → start-onboarding response
POST   /api/v1/onboarding/reauth/qr/{sender_id}                                              → QR response
```

### Senders (план 02-02)

```
GET    /api/v1/senders                        → [{id, slug, name, phone, status (derived), auth_status,
                                                 lifecycle_status, rate_limits: {per_minute, per_hour, per_day},
                                                 proxy, ...}]
GET    /api/v1/senders/{id}                   → single
POST   /api/v1/senders                        {slug, name, phone, session_string, role, ai_context_id?, proxy?,
                                                rate_per_min?, rate_per_hour?, rate_per_day?}
                                              → 201 SenderResponse (с warnings[] если rate выше green corridor)
PATCH  /api/v1/senders/{id}                   {name?, lifecycle_status?, rate_per_min?, ..., proxy?, ai_context_id?}
                                              → 200 SenderResponse + warnings[]
                                              → 422 если выше hard cap
DELETE /api/v1/senders/{id}                   → 204 (cascade удаляет conversations, messages, contacts_cache, queue items)
POST   /api/v1/senders/{id}/assign-proxy      {proxy_id}              → 200 SenderResponse (proxy from pool)
GET    /api/v1/senders/{id}/spambot-check                              → {status, raw_text, auth_status_updated?}
GET    /api/v1/workspace/proxies                                       → list (D-22)
POST   /api/v1/workspace/proxies              {host, port, type, username?, password?} → 201
DELETE /api/v1/workspace/proxies/{id}                                  → 204
```

### Folders (план 02-03)

```
GET    /api/v1/folders                                  → [{id, name, contact_count, created_at}]
POST   /api/v1/folders                {name}            → 201 FolderResponse
PATCH  /api/v1/folders/{id}           {name}            → 200 FolderResponse
DELETE /api/v1/folders/{id}[?force=true]                → 204 OR 409 {contact_count, active_campaigns: []}
```

### Contacts (планы 02-04, 02-05)

```
GET    /api/v1/contacts?folder_id={id}&tg_status={s}&limit={n}&cursor={c}
                                                        → cursor pagination
POST   /api/v1/contacts                                 single contact или batch (до 1000)
                                                        — push API через X-Workspace-Key (D-10)
                                                        body: {contacts: [{phone, username, full_name, source, custom,
                                                               folder_name | folder_id}, ...]}
                                                        или single: {phone, ...}
                                                        → {imported: N, skipped_duplicates: M, skipped_invalid: K}

POST   /api/v1/contacts/import/preview multipart        {file: csv} → {import_id, columns, sample_rows,
                                                                       suggested_mapping, encoding, delimiter}
POST   /api/v1/contacts/import         JSON             {import_id, folder_id | folder_name, mapping, on_duplicate}
                                                        → 202 Accepted {imported, skipped_*, total}
POST   /api/v1/contacts/recheck                         {contact_ids: [...]} | {folder_id: ...}
                                                        → 202 {marked_pending: N}
POST   /api/v1/contacts/{id}/move                       {folder_id} → 200
POST   /api/v1/contacts/move (batch)                    {contact_ids, folder_id} → {moved: N}
DELETE /api/v1/contacts/{id}                             → 204
DELETE /api/v1/contacts (batch)                          {contact_ids} → {deleted: N}
```

### Workspace (расширение Phase 1)

```
GET    /api/v1/workspace                                → {id, name, has_checker, ...}
                                                          (новое поле has_checker — D-20)
```

**Что planner должен делать в каждом PLAN.md:** для каждого endpoint выписать Pydantic-схему (request + response), error-codes, примеры payload'а, edge-cases (особенно валидация E.164, mapping без phone/username, batch > 1000). Это контракт для Lovable-команды.

## Standard Stack

### Core (уже установлено)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| FastAPI | 0.109.0 | Web framework | Уже в requirements.txt, async-first. **Версия НЕ проверена против registry — может быть устаревшей; в Phase 2 НЕ обновляем (вне scope).** |
| SQLAlchemy | 2.0.25 async | ORM | Async-first 2.0 API, уже используется. |
| asyncpg | 0.29.0 | PostgreSQL driver | Async. |
| Telethon | 1.42.0 | Telegram MTProto | Январь 2024, актуальная версия на момент Phase 1. |
| python-jose[cryptography] | 3.3.0 | JWT decode | Phase 1 D-05 — HS256 для Supabase. |
| bcrypt | >=4.1.0,<5.0 | Workspace API key hash | Phase 1 D-13. |
| pydantic | >=2.8,<3.0 | Validation | v2 + ConfigDict. |
| pydantic-settings | >=2.3,<3.0 | Env config | `Settings` в `app/config.py`. |
| httpx | 0.26.0 | Async HTTP | Webhook callbacks. |
| openai | >=1.40.0,<2.0.0 | LLM (Phase 3+) | Не Phase 2. |
| qrcode[pil] | 7.4.2 | QR-онбординг | Уже используется в onboarding.py. |
| PySocks | 1.7.1 | SOCKS5 proxy | Telethon proxy. |
| cryptography | 42.0.0 | Fernet for session encryption | `encrypt_session` / `decrypt_session`. |
| email-validator | 2.1.0 | Pydantic email | Уже там. |
| python-multipart | 0.0.6 | Multipart upload | **Нужно для `POST /contacts/import/preview` file upload** — ПРОВЕРИТЬ что fastapi требует — уже в requirements (line 4). |
| pytest, pytest-asyncio | >=8.0, >=0.23 | Tests | Phase 1 D-17. |

### Phase 2 НЕ добавляет новых deps

- **csv** — stdlib (для CSV-импорта).
- **io.BytesIO / StringIO** — stdlib.
- **re** — stdlib (phone normalization, mapping aliases).
- **secrets, hashlib, uuid** — stdlib.

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `csv.DictReader` | `pandas` | Pandas overkill — 3+ MB деп, slow startup. v1 не требует. |
| Manual phone regex | `phonenumbers` | phonenumbers — 5+ MB data, авторитетная либо для глобальной валидации. Для v1 (RU-centric) regex+heuristic достаточно. Если в v1.5 появятся проблемы — добавим. |
| `BYTEA в DB` для CSV | `/tmp/{id}` | Discussed — recommend BYTEA. |
| `SQLEnum` для статусов | `String + CHECK` | Discussed — recommend String + CHECK (легче расширять, консистентно с Phase 1 `user_workspaces.role`). |

**Installation (для Phase 2 — NOTHING NEW):**

```bash
# Уже всё установлено через Phase 1's requirements.txt
# Если планер захочет phonenumbers (НЕ рекомендую):
# echo "phonenumbers==8.13.49" >> requirements.txt
```

## Architecture Patterns

### Recommended Project Structure (после Phase 2)

```
app/
├── main.py                          # FastAPI app — расширяется новыми include_router (5 новых)
├── config.py                        # Settings (без изменений)
├── database.py                      # AsyncSessionLocal, get_db
├── models/
│   └── __init__.py                  # +Folder, +Contact, +OnboardingSession + расширение Sender
├── schemas/
│   └── __init__.py                  # +ContactCreate, +ContactImportRequest, +ContactImportPreviewResponse,
│                                    #  +FolderResponse, +OnboardingStart, расширение SenderUpdate
├── utils/
│   ├── auth.py                      # AuthDep (Phase 1, без изменений)
│   └── phone.py                     # NEW: normalize_to_e164
├── routers/
│   ├── health.py                    # Phase 1 — обновить на lifecycle_status
│   ├── workspace.py                 # Phase 1 — расширить has_checker; +proxy CRUD
│   ├── onboarding.py                # REWRITE: AuthDep + onboarding_sessions table
│   ├── senders.py                   # REWRITE: AuthDep + rate_per_*, lifecycle_status, derived status, warnings
│   ├── folders.py                   # NEW: CRUD папок
│   ├── contacts.py                  # NEW: list, create (push), import/preview, import, move, recheck, delete
│   └── check_contacts.py            # REWRITE: AuthDep + recheck endpoint, фоновый worker через service
└── services/
    ├── queue.py                     # MODIFY: rate-limit constants → sender.rate_per_*
    ├── listener.py                  # MODIFY: добавить _reconcile_loop, filter lifecycle_status
    ├── telegram.py                  # NO CHANGE
    ├── encryption.py                # NO CHANGE
    ├── checker.py                   # NO CHANGE (переиспользуется ContactCheckWorker'ом)
    ├── warmup.py                    # MODIFY: filter is_active → lifecycle_status + auth_status
    ├── rotation.py                  # MODIFY: один SQL-фильтр (line 48, 147)
    ├── contact_check_worker.py      # NEW: фоновая задача в lifespan
    ├── csv_import.py                # NEW: parse_preview, suggest_mapping, apply_import
    └── onboarding_state.py          # NEW: CRUD helpers для onboarding_sessions table + TTL cleanup

migrations/
├── 012_workspace.sql                # Phase 1
└── 013_phase2.sql                   # NEW: contacts, folders, onboarding_sessions,
                                     #      senders.rate_per_*, senders.lifecycle_status, drop is_active,
                                     #      contacts unique partial indexes, role CHECK constraint

tests/
├── conftest.py                      # EXTEND: новые factory-fixtures (см. §"pytest Fixtures")
├── test_auth_dep.py                 # Phase 1
├── test_workspace_router.py         # Phase 1
├── test_workspace_api_keys.py       # Phase 1
├── test_migration_012.py            # Phase 1
├── test_migration_013.py            # NEW: smoke (все таблицы созданы, FK работают, CHECK constraints отвергают bad values)
├── test_onboarding.py               # NEW: 02-01
├── test_senders.py                  # NEW: 02-02 (rate-limit warnings, lifecycle переходы, derived status)
├── test_folders.py                  # NEW: 02-03 (CRUD + 409 conflict + force=true)
├── test_contacts.py                 # NEW: 02-04 (CSV preview/import + push + dedup + folder auto-create)
├── test_contact_check_worker.py     # NEW: 02-05 (worker tick, pending → registered/not_registered, FloodWait handling)
├── test_phone_normalization.py      # NEW: utils/phone.py
└── test_listener_reconcile.py       # NEW: 02-01 (mock сессий, reconcile добавляет/убирает clients)
```

### Pattern 1: Background asyncio task в lifespan

**What:** Singleton class с `start()/stop()` методами, создаётся at module level, регистрируется в FastAPI `lifespan`.

**When to use:** ContactCheckWorker, OnboardingSessionCleanup, любой periodic job.

**Source:** `app/services/queue.py` QueueWorker (line 67-107), `app/services/warmup.py` WarmupWorker.

```python
# app/services/contact_check_worker.py
import asyncio
import logging
from typing import Optional
from app.database import AsyncSessionLocal
from app.services.checker import checker_service

logger = logging.getLogger(__name__)


class ContactCheckWorker:
    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self.batch_size = 5
        self.poll_interval = 5  # seconds
    
    def start(self):
        if self._task is None or self._task.done():
            self._running = True
            self._task = asyncio.create_task(self._run(), name="contact-check-worker")
            logger.info("ContactCheckWorker started")
    
    async def stop(self):
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("ContactCheckWorker stopped")
    
    async def _run(self):
        while self._running:
            try:
                await self._tick()
            except Exception as exc:
                logger.error(f"ContactCheckWorker error: {exc}", exc_info=True)
            await asyncio.sleep(self.poll_interval)
    
    async def _tick(self):
        # ... см. §"ContactCheckWorker"
        ...


contact_check_worker = ContactCheckWorker()
```

В `app/main.py` lifespan:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup
    await init_db()
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
```

### Pattern 2: Workspace isolation в каждом query

**What:** Все SELECT/UPDATE/DELETE на tenant-таблицах должны иметь `.where(Model.workspace_id == ctx.workspace_id)`.

**Source:** Phase 1 D-04 + AuthCtx Phase 1 D-12. Аналог в Phase 1 — `app/routers/workspace.py` все queries.

```python
# Пример из плана 02-04
@router.get("")
async def list_contacts(
    folder_id: UUID | None = None,
    tg_status: str | None = None,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    q = select(Contact).where(Contact.workspace_id == ctx.workspace_id)  # TODO(v2-rls)
    if folder_id is not None:
        q = q.where(Contact.folder_id == folder_id)  # folder уже принадлежит workspace через FK + isolation
    if tg_status is not None:
        q = q.where(Contact.tg_status == tg_status)
    result = await db.execute(q.order_by(Contact.created_at.desc()))
    return [ContactResponse.model_validate(c) for c in result.scalars().all()]
```

### Pattern 3: Pydantic v2 ConfigDict + Literal для enum-like polya

```python
# app/schemas/__init__.py
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

class FolderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    
    id: UUID
    name: str
    contact_count: int = 0
    created_at: datetime

class SenderUpdate(BaseModel):
    name: str | None = None
    lifecycle_status: Literal['active', 'warmup', 'paused'] | None = None
    rate_per_min: int | None = Field(None, ge=1, le=10)
    rate_per_hour: int | None = Field(None, ge=1, le=50)
    rate_per_day: int | None = Field(None, ge=1, le=300)
    proxy: ProxyConfig | None = None
    ai_context_id: UUID | None = None
```

### Anti-Patterns to Avoid

- **НЕ хранить `_onboarding_sessions: dict`** — был основной concern Phase 1 CONCERNS.md.
- **НЕ `subprocess.run(["docker", "restart", ...])`** — нарушает single-process boundary.
- **НЕ `Sender.role = String(20)` без CHECK** — добавить CHECK в миграцию 013.
- **НЕ дублировать workspace-isolation** — каждый запрос к tenant-таблице фильтрует по `workspace_id`. Можно сделать helper `get_db_scoped(ctx)`, но Phase 1 решил это не делать (CONTEXT.md Phase 1 D-04).
- **НЕ путать `contacts` (новая workspace-level таблица) и `contacts_cache` (per-sender resolve cache)** — D-01 явно говорит.
- **НЕ `time.sleep()` нигде** — только `await asyncio.sleep(...)`.
- **НЕ `print()` для отладки** — `logger.info/debug/warning/error`.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| JWT decode | Кастомный HS256 | `python-jose` (уже Phase 1) | Тесты edge-cases (kid, audience, exp). |
| Password hash | Custom bcrypt loop | `bcrypt.checkpw` через `asyncio.to_thread` (Phase 1 уже сделал) | Не блокировать event loop. |
| CSV parsing | Кастомный split-by-comma | `csv.DictReader` (stdlib) | Quoting, escaping, dialect detection. |
| CSV dialect detection | Манульное распознавание | `csv.Sniffer().sniff(sample, delimiters=...)` | Гарантированные edge-cases. |
| QR-код генерация | Кастомные SVG | `qrcode` package (уже в reqs) | Уже работает в onboarding.py. |
| Phone normalization | None для глобальных номеров | Кастомный regex для **RU/СНГ-focus** + `+` + 7..15 digits | `phonenumbers` overkill для v1; RU-heuristic явная. |
| Telegram MTProto | Низкий уровень | `telethon` (уже там) | Только Telethon (`pyrogram` — альтернатива, но менять не нужно). |
| Background scheduler | `apscheduler`, `celery`, `arq` | `asyncio.create_task` в lifespan | Pattern уже в проекте — QueueWorker, WarmupWorker. |
| File upload streaming | Custom | FastAPI `UploadFile` | Async, memory-efficient. |
| ENUM types | Postgres `CREATE TYPE` | `VARCHAR + CHECK constraint` | Idempotent migrations, легко расширять. |

**Key insight:** Стек Python-стандартной библиотеки + 5 ключевых либ (FastAPI, SQLAlchemy, Telethon, jose, bcrypt) полностью покрывает Phase 2. Никаких новых deps. Это удешевляет ревью, тесты и деплой.

## Common Pitfalls

### Pitfall 1: ImportError при попытке include_router старого роутера

**Что идёт не так:** План 02-01 переписывает `app/routers/onboarding.py`. Если planner забудет, что `verify_api_key` нет в `app/routers/auth.py`, и попробует постепенно мигрировать — `import` упадёт.

**Почему случается:** Phase 1 D-14 удалил `app/routers/auth.py` целиком, но оставил `from app.routers.auth import verify_api_key` в 9 файлах.

**Как избегать:** План 02-01 (и 02-02, 02-05) начинается с **полной замены** import-блока в перерайтываемых файлах: `from app.routers.auth import verify_api_key` → `from app.utils.auth import auth_dep, AuthCtx`.

**Warning signs:** При первом запуске `pytest` или `uvicorn` после рерайта — `ModuleNotFoundError: No module named 'app.routers.auth'`.

### Pitfall 2: Telethon client lost после рестарта API контейнера во время онбординга

**Что идёт не так:** Юзер на шаге verify_code, API контейнер рестартанул (deploy, OOM). Dict `_in_process_clients` пуст. Если planner забыл D-17 recovery — `404 SESSION_NOT_FOUND`, юзер должен начать с send_code_request.

**Почему случается:** Telethon-client объект НЕ сериализуется (содержит open TCP, internal state).

**Как избегать:** При получении verify_code:
1. Сначала смотреть в in-process dict.
2. Если нет → SELECT из `onboarding_sessions` table.
3. Если row есть и `status='code_sent'` → `make_telegram_client(StringSession(decrypt_session(row.encrypted_session_string)), proxy=row.proxy); await client.connect(); await client.sign_in(phone, code, phone_code_hash)`.
4. Если sign_in успешен → продолжить normal flow.
5. **Save session_string в onboarding_sessions ПОСЛЕ send_code_request** (новое — текущий код не сохраняет до verify_code).

**Warning signs:** В логах "Сессия не найдена или истекла" при имеющейся row в `onboarding_sessions` table.

### Pitfall 3: FloodWait при ResolvePhone — checker умирает на минуты/часы

**Что идёт не так:** ContactCheckWorker batch_size=20 без polite delay → Telegram бьёт FloodWait 600 секунд. Worker зависает.

**Почему случается:** Resolve без polite delay — Telegram считает как DoS-pattern.

**Как избегать:** Используется существующий `CheckerService.check_phones` (line 207-209) с `random.uniform(2.0, 3.5)` между phones. При FloodWaitError — partial result, sleep `e.seconds`, выход из батча. Worker на следующем tick'е продолжит с оставшимися pending контактами.

**Warning signs:** `[checker:slug] FloodWait hit after X/Y phones — sleeping Z`. Если Z > 600 → checker деактивирован Telegram'ом (banned/limited), нужна реавторизация.

### Pitfall 4: Race condition при auto-create folder по `folder_name`

**Что идёт не так:** Два параллельных push'а через Workspace API с одним `folder_name="Leads"`. Оба видят "folder не существует" → оба создают. UNIQUE `(workspace_id, name)` спасает на DB-уровне, но один из push'ей упадёт с 500 IntegrityError.

**Почему случается:** Check-then-insert без транзакции.

**Как избегать:** `INSERT ... ON CONFLICT (workspace_id, name) DO NOTHING RETURNING id` (Postgres-specific). Если RETURNING пуст → folder уже создан, SELECT его id.

```python
# Пример
row = await db.execute(
    text("""
        INSERT INTO folders (workspace_id, name)
        VALUES (:wid, :name)
        ON CONFLICT (workspace_id, name) DO UPDATE SET updated_at = NOW()
        RETURNING id
    """),
    {"wid": str(ctx.workspace_id), "name": folder_name}
)
folder_id = row.scalar()
```

**Warning signs:** `IntegrityError: duplicate key value violates unique constraint "folders_workspace_id_name_key"`.

### Pitfall 5: Listener reconcile loop теряет состояние proxy при изменении

**Что идёт не так:** Юзер делает `POST /senders/{id}/assign-proxy {proxy_id=X}`. Через 30 секунд reconcile запускается. Sender был active+ok → теперь active+ok с другим proxy. Reconcile видит "ничего не изменилось" (sender уже в `currently_connected`) — но **client всё ещё подключён через старый proxy**.

**Почему случается:** Reconcile сравнивает set'ы id, не сравнивает атрибуты.

**Как избегать:** Хранить `_proxy_snapshot: dict[sender_id, dict | None]` — при каждом connect записываем фактический proxy, в reconcile сравниваем `desired.proxy != self._proxy_snapshot[sid]` → удаляем из dict (как будто sender ушёл) → next tick подключает с новым.

**Warning signs:** Юзер сменил proxy, но IP остался тот же (через `myip.ru` Telethon-сообщения).

### Pitfall 6: CSV preview file потерян при рестарте контейнера

**Что идёт не так (Вариант A `/tmp`):** Юзер сделал preview в 14:00, выбрал mapping, ушёл на обед. В 14:15 deploy рестартанул контейнер → `/tmp/{import_id}` потерян. В 15:00 юзер кликает "Import" → 404.

**Mitigation:** Вариант B (BYTEA в DB) этого избегает (см. §"CSV Import Storage").

### Pitfall 7: Конфликт `senders.is_active` при миграции 013

**Что идёт не так:** Миграция 013 дропает колонку `senders.is_active`. Если в момент миграции старый код где-то делает `INSERT INTO senders (..., is_active, ...) VALUES (..., true, ...)` — ошибка.

**Как избегать:** В Phase 1 все старые роутеры выпилены из `main.py`. БД пустая (Phase 1 D-01). Но `app/models/__init__.py:84` всё ещё описывает `is_active`. План 02-02 одновременно:
1. Меняет ORM-модель (удаляет `is_active` Column).
2. Пишет миграцию 013 с `ALTER TABLE senders DROP COLUMN IF EXISTS is_active`.
3. Меняет 14 мест использования (см. §"Hidden Dependencies").

Эти 3 шага идут в одном PR/коммите — никогда раздельно.

### Pitfall 8: AuthCtx неправильно работает с `X-Workspace-Key` если plan забыл `Depends(auth_dep)` 

**Что идёт не так:** План 02-04 пишет `POST /contacts` — путаница: пытается через "JWT-only" auth, но n8n push ходит через `X-Workspace-Key`. Если planner не использует `Depends(auth_dep)` (а напрямую JWT validation), API-key fail.

**Как избегать:** ВСЕ новые endpoint'ы (включая push) используют один и тот же `Depends(auth_dep)`. AuthDep сам branch'ится по заголовку.

**Warning signs:** n8n тесты возвращают 401 для валидного `X-Workspace-Key`.

## Runtime State Inventory

> Phase 2 — это в основном новые таблицы и переписывание роутеров. Но миграция дропает `senders.is_active`, поэтому проверим runtime state.

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| **Stored data** | БД пустая (Phase 1 D-01 подтверждено; Phase 1 успешно завершён 2026-05-21). После Phase 1 в БД только Workspace, UserWorkspace, WorkspaceApiKey + пустые tenant-таблицы. **Нет реальных senders, нет реальных контактов, нет onboarding session'ов.** | None — миграция 013 идёт на чистую БД. Aggressive constraints (UNIQUE phone в workspace) ничего не нарушают. |
| **Live service config** | n8n workflows (внешний сервис) — могут содержать упоминания старого API endpoint'а (`/api/v1/check-contacts` со старым `X-API-Key`). | n8n flows должны быть обновлены клиентами вручную после Phase 2 — это вне scope. Но: пометить в DOCS.md что API "Push contacts" теперь использует `X-Workspace-Key: wsk_...` + JSON `{contacts: [...]}` (D-10). |
| **OS-registered state** | None. Outreach-platform — Docker Compose стек, нет cron'ов или OS-services вне containers. | None. |
| **Secrets / env vars** | `SUPABASE_JWT_SECRET`, `SUPABASE_URL`, `CORS_ALLOWED_ORIGINS`, `DATABASE_URL`, `ENCRYPTION_KEY`, `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `OPENAI_API_KEY` — все Phase 1 уже использует. **Не добавляются новые env vars в Phase 2.** Возможно добавить `LISTENER_RECONCILE_INTERVAL` (default 30, см. C-06) и `CONTACT_CHECK_BATCH_SIZE` (default 5) — но это optional. | None для существующих. Planner может добавить 2 optional env vars. |
| **Build artifacts** | `__pycache__/` папки — игнорируются git'ом. **Docker image rebuild требуется после Phase 2** (новый код + новые зависимости — но deps те же, поэтому только code-rebuild). | После деплоя: `docker compose up -d --build api` + `docker compose up -d --build listener` (CLAUDE.md). |

**Specific concern — `docker.sock` mount:**

Phase 2 D-18 выпиливает `subprocess.run(["docker", "restart", "telegram-listener"])`. Это значит, что в `docker-compose.yml` для сервиса `api` **больше НЕ нужен mount `/var/run/docker.sock`**. Если этот mount был добавлен ранее для существующего хака — план 02-01 (или Wave 0) должен удалить эту строку из `docker-compose.yml`. **Action для planner:** в плане 02-01 включить checklist item "Verify and remove `docker.sock` volume mount from `api` service in `docker-compose.yml` if present".

## Code Examples

### Migration 013 (skeleton, ~150 строк raw SQL)

```sql
-- migrations/013_phase2.sql
-- Phase 2: TG Accounts & Contacts foundation
-- Adds: contacts, folders, onboarding_sessions, csv_imports tables
-- Modifies: senders (+ rate_per_*, lifecycle_status; - is_active)
-- БД пустая (Phase 1 D-01). Все операторы идемпотентны (IF NOT EXISTS / IF EXISTS).

BEGIN;

-- ── 1. folders ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS folders (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name          VARCHAR(100) NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT folders_workspace_name_unique UNIQUE (workspace_id, name)
);

CREATE INDEX IF NOT EXISTS idx_folders_workspace ON folders(workspace_id);

-- ── 2. contacts ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS contacts (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id          UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    folder_id             UUID NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
    phone                 VARCHAR(20),
    username              VARCHAR(50),
    full_name             VARCHAR(200),
    source                VARCHAR(100),
    custom                JSONB NOT NULL DEFAULT '{}'::jsonb,
    tg_status             VARCHAR(20) NOT NULL DEFAULT 'pending',
    tg_telegram_id        BIGINT,
    tg_username_resolved  VARCHAR(50),
    tg_error              TEXT,
    tg_checked_at         TIMESTAMPTZ,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT contacts_tg_status_check
        CHECK (tg_status IN ('pending', 'registered', 'not_registered', 'error', 'unchecked')),
    CONSTRAINT contacts_phone_or_username_check
        CHECK (phone IS NOT NULL OR username IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_contacts_workspace ON contacts(workspace_id);
CREATE INDEX IF NOT EXISTS idx_contacts_folder ON contacts(folder_id);
CREATE INDEX IF NOT EXISTS idx_contacts_tg_status ON contacts(tg_status) WHERE tg_status = 'pending';

-- D-02: уникальность по workspace + phone (только не-NULL phone)
CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_workspace_phone_unique
    ON contacts(workspace_id, phone) WHERE phone IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_workspace_username_unique
    ON contacts(workspace_id, username) WHERE username IS NOT NULL;

-- ── 3. onboarding_sessions (D-16) ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS onboarding_sessions (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id                UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    phone                       VARCHAR(20) NOT NULL,
    phone_code_hash             TEXT NOT NULL,
    encrypted_session_string    TEXT NOT NULL,
    role                        VARCHAR(20) NOT NULL DEFAULT 'sender',
    proxy                       JSONB,
    status                      VARCHAR(20) NOT NULL,
    expires_at                  TIMESTAMPTZ NOT NULL,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT onboarding_sessions_role_check
        CHECK (role IN ('sender', 'checker')),
    CONSTRAINT onboarding_sessions_status_check
        CHECK (status IN ('code_sent', 'awaiting_2fa', 'completed', 'failed'))
);

CREATE INDEX IF NOT EXISTS idx_onboarding_sessions_workspace ON onboarding_sessions(workspace_id);
CREATE INDEX IF NOT EXISTS idx_onboarding_sessions_expires_at ON onboarding_sessions(expires_at);

-- ── 4. csv_imports (recommended Option B, см. RESEARCH §"CSV Import Storage") ─
CREATE TABLE IF NOT EXISTS csv_imports (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id        UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    file_data           BYTEA NOT NULL,
    columns             JSONB NOT NULL,
    suggested_mapping   JSONB NOT NULL,
    encoding            VARCHAR(20),
    delimiter           VARCHAR(5),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at          TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '30 minutes')
);

CREATE INDEX IF NOT EXISTS idx_csv_imports_workspace ON csv_imports(workspace_id);
CREATE INDEX IF NOT EXISTS idx_csv_imports_expires_at ON csv_imports(expires_at);

-- ── 5. Extend senders (D-11, D-13) ────────────────────────────────────────
-- D-11: добавляем lifecycle_status, дропаем is_active
ALTER TABLE senders
    ADD COLUMN IF NOT EXISTS lifecycle_status VARCHAR(20) NOT NULL DEFAULT 'active';

ALTER TABLE senders
    DROP CONSTRAINT IF EXISTS senders_lifecycle_status_check;
ALTER TABLE senders
    ADD CONSTRAINT senders_lifecycle_status_check
        CHECK (lifecycle_status IN ('active', 'warmup', 'paused'));

-- D-13: rate_per_* поля с server_default
ALTER TABLE senders
    ADD COLUMN IF NOT EXISTS rate_per_min INT NOT NULL DEFAULT 4;
ALTER TABLE senders
    ADD COLUMN IF NOT EXISTS rate_per_hour INT NOT NULL DEFAULT 20;
ALTER TABLE senders
    ADD COLUMN IF NOT EXISTS rate_per_day INT NOT NULL DEFAULT 150;

-- CONTEXT.md <specifics>: role CHECK constraint
ALTER TABLE senders
    DROP CONSTRAINT IF EXISTS senders_role_check;
ALTER TABLE senders
    ADD CONSTRAINT senders_role_check
        CHECK (role IN ('sender', 'checker'));

-- D-11 финал: drop is_active
ALTER TABLE senders
    DROP COLUMN IF EXISTS is_active;

COMMIT;
```

### Telethon onboarding flow (новый router skeleton)

```python
# app/routers/onboarding.py — REWRITE skeleton
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timezone, timedelta
from uuid import uuid4

from app.database import get_db
from app.utils.auth import auth_dep, AuthCtx
from app.models import OnboardingSession, Sender
from app.services.telegram import make_telegram_client
from app.services.encryption import encrypt_session, decrypt_session
from telethon.sessions import StringSession
from telethon.errors import (
    PhoneCodeInvalidError, PhoneCodeExpiredError, SessionPasswordNeededError,
    PasswordHashInvalidError, FloodWaitError, PhoneNumberBannedError
)
import logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/onboarding", tags=["onboarding"])

# In-process dict для TelegramClient объектов (D-17): сериализация невозможна.
# Recovery from DB происходит при cache miss.
_in_process_clients: dict[str, "TelegramClient"] = {}


@router.post("/start")
async def start_onboarding(
    request: StartRequest,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    phone = _normalize_phone(request.phone)
    if not phone:
        raise HTTPException(400, {"code": "PHONE_INVALID"})
    
    proxy = await _get_free_proxy_for_workspace(db, ctx.workspace_id)
    
    client = make_telegram_client(StringSession(), proxy=proxy.model_dump() if proxy else None)
    try:
        await client.connect()
        sent_code = await client.send_code_request(phone)
    except FloodWaitError as e:
        await client.disconnect()
        raise HTTPException(429, {"code": "FLOOD_WAIT", "retry_after": e.seconds})
    except PhoneNumberBannedError:
        await client.disconnect()
        raise HTTPException(400, {"code": "PHONE_NUMBER_BANNED"})
    
    # Persist state — критично для D-17 recovery
    onboarding_id = uuid4()
    session = OnboardingSession(
        id=onboarding_id,
        workspace_id=ctx.workspace_id,
        phone=phone,
        phone_code_hash=sent_code.phone_code_hash,
        encrypted_session_string=encrypt_session(client.session.save()),
        proxy=proxy.model_dump() if proxy else None,
        role="sender",  # будет переопределён в verify-code
        status="code_sent",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    db.add(session)
    await db.commit()
    
    _in_process_clients[str(onboarding_id)] = client
    logger.info(f"📱 Onboarding started: phone={phone[:6]}*** session={str(onboarding_id)[:8]}...")
    
    return {"session_id": str(onboarding_id), "status": "code_sent"}


@router.post("/verify-code")
async def verify_code(
    request: VerifyCodeRequest,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    # 1. Load state (workspace-isolated!)
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
    
    # 3. sign_in
    try:
        await client.sign_in(
            phone=session_row.phone, code=request.code, phone_code_hash=session_row.phone_code_hash,
        )
    except PhoneCodeInvalidError:
        raise HTTPException(400, {"code": "PHONE_CODE_INVALID"})
    except PhoneCodeExpiredError:
        session_row.status = "failed"
        await db.commit()
        raise HTTPException(400, {"code": "PHONE_CODE_EXPIRED"})
    except SessionPasswordNeededError:
        # Save updated session_string (post-attempt) и role
        session_row.role = request.role or "sender"
        session_row.encrypted_session_string = encrypt_session(client.session.save())
        session_row.status = "awaiting_2fa"
        await db.commit()
        return {"status": "2fa_required"}
    except FloodWaitError as e:
        raise HTTPException(429, {"code": "FLOOD_WAIT", "retry_after": e.seconds})
    
    # 4. Success — create sender + cleanup
    sender = Sender(
        workspace_id=ctx.workspace_id,
        slug=request.slug,                                 # planner: либо просит slug, либо auto-generate
        name=request.name or session_row.phone,
        phone=session_row.phone,
        session_string=encrypt_session(client.session.save()),
        role=request.role or "sender",
        auth_status="ok",
        lifecycle_status="active",
        proxy=session_row.proxy,
        rate_per_min=4, rate_per_hour=20, rate_per_day=150,
    )
    db.add(sender)
    session_row.status = "completed"
    await db.commit()
    
    # Cleanup in-process + disconnect (listener reconcile-loop сам подцепит нового sender'а в ≤30 сек)
    _in_process_clients.pop(sid_str, None)
    await client.disconnect()
    
    return {"status": "success", "sender_id": str(sender.id), "role": sender.role}
```

### ContactCheckWorker tick

```python
# app/services/contact_check_worker.py — внутри _tick
async def _tick(self):
    async with AsyncSessionLocal() as db:
        # Берём pending контакты + объединяем с checker'ом workspace'а
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
            return  # nothing to do
    
    # Group by checker (one workspace might have multiple checkers in v2; для v1 один)
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
        
        # Apply results to contacts table
        async with AsyncSessionLocal() as db:
            for item, result in zip(items, summary["results"]):
                tg_status = "registered" if result["is_registered"] else "not_registered"
                await db.execute(text("""
                    UPDATE contacts
                    SET tg_status = :status,
                        tg_telegram_id = :tg_id,
                        tg_checked_at = NOW(),
                        updated_at = NOW()
                    WHERE id = :cid
                """), {
                    "status": tg_status,
                    "tg_id": result.get("telegram_id"),
                    "cid": item.id,
                })
            await db.commit()
        
        logger.info(
            f"📋 ContactCheckWorker: checker={first.checker_slug} "
            f"checked={summary['checked']} reg={summary['registered']} "
            f"not_reg={summary['not_registered']} flood={summary['flood_wait_hit']}"
        )
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Глобальные rate-limit константы в queue.py | Per-sender `rate_per_*` в БД | Phase 2 D-13 | UI может конфигурировать; multi-tenant aware. |
| `subprocess.run(["docker","restart"])` для listener reload | Periodic reconcile loop в listener.py | Phase 2 D-18 | Нет нужды в `docker.sock` mount; safer; portable. |
| `_onboarding_sessions: dict` (in-memory) | `onboarding_sessions` table + in-process clients | Phase 2 D-16/17 | API-контейнер можно рестартить без потери flow. |
| Global `X-API-Key` для всех каллеров | Dual auth: JWT (UI) + workspace API-key | Phase 1 D-11 (already done) | Multi-tenant. |
| `senders.is_active` boolean | Derived `status: active|warmup|paused|error` | Phase 2 D-11 | Юзер видит разные состояния, листенер автоматом помечает error. |
| `Sender.role` String без CHECK | String + CHECK constraint | Phase 2 (CONTEXT `<specifics>`) | DB-level enforcement, no Postgres ENUM. |
| Telegram check через `ImportContactsRequest` (старый код в `app/services/telegram.py:401`) | `ResolvePhoneRequest` (новый код, line 357) | Уже сделано до Phase 2 | Меньше шансов на ban (ResolvePhone не добавляет в контакты). |

**Deprecated / outdated в коде:**

- `Sender.is_active` — дроп в миграции 013.
- Global `X-API-Key` (Phase 1 уже выпилил `verify_api_key`).
- Hardcoded working hours 09–20 МСК (`queue.py:62-63`) — переезд на campaign-level в Phase 4 (CONTEXT.md `<deferred>` явно). НЕ В PHASE 2 SCOPE.

## Open Questions

1. **Onboarding QR-flow recovery после рестарта API контейнера** — реально ли поддерживать, и в каком плане?
   - Что знаем: QR-объект Telethon (`qr_login = await client.qr_login()`) держит auth_token внутри. Сериализация в БД потребует приватного API Telethon (`qr_login._token`?), который может сломаться при обновлении версии Telethon.
   - Что неясно: насколько часто рестарты api-контейнера попадают в 30-60-секундное окно сканирования QR.
   - Рекомендация: **не реализовывать persistent QR в Phase 2**. При SESSION_NOT_FOUND UI начинает новый QR. Документировать как known limitation.

2. **Auto-recheck unchecked контактов при появлении первого checker'а** — D-20 деферрит на v2, но в plan'е 02-05 нужно ли оставлять hook?
   - Что знаем: D-20 явно — "auto-recheck при появлении checker'а — деферрим".
   - Что неясно: должен ли `POST /senders` с `role='checker'` триггерить marking всех `unchecked` → `pending`?
   - Рекомендация: **нет** — ровно по D-20. Юзер делает явный `POST /contacts/recheck {folder_id: ...}` или `{contact_ids: [...]}`.

3. **Что с `app/routers/auth.py`** — удалён физически или есть пустой stub?
   - Что знаем: `app/routers/auth.py` физически отсутствует в дереве файлов (проверено `ls app/routers/` — нет такого файла).
   - Что неясно: должен ли план 02-01/02-02 явно удалять оставшиеся `from app.routers.auth import verify_api_key` из неперевывариваемых старых файлов (например, `send.py`, `queue.py`-router, `conversations.py`, `contexts.py`, `warmup.py`, `proxy_pool.py`), которые в Phase 2 НЕ рерайтятся?
   - Рекомендация: **не трогать** broken-import файлы в Phase 2. Они уже не include_router'ятся в main.py — ImportError не достигается. План 02-* рерайтит ТОЛЬКО onboarding/senders/check_contacts/folders/contacts. Остальные 6 router-файлов — Phase 3 (contexts → agents, warmup), Phase 4 (send, queue, conversations).

4. **Имя schema-классов** — конвенция `ContactCreate / ContactImportRequest / ContactImportPreviewResponse` vs `ContactPushIn / ContactImportIn / ContactImportPreviewOut`?
   - Что знаем: существующий стиль (`SendMessageRequest / SendMessageResponse / SenderCreate / SenderUpdate / SenderResponse`) — PascalCase, суффиксы `Request/Response/Create/Update`. С 2.0 Pydantic — `model_config = ConfigDict(from_attributes=True)` для response-схем.
   - Рекомендация: следовать существующему стилю. C-04 даёт planner'у свободу.

5. **Контакт с `phone IS NULL AND username IS NOT NULL`** — может ли он быть на `tg_status='pending'` или сразу `'registered'` (если username уже резолвлен в TG)?
   - Что знаем: `ResolveUsernameRequest` — отдельный API. Checker может его вызывать.
   - Что неясно: фактическая семантика `tg_status` для username-only контактов в v1.
   - Рекомендация: planner 02-05 решает — простейший вариант: pending → checker делает `ResolveUsernameRequest` → registered/not_registered. Логика та же что для phone. Можно объединить в одном tick'е (но проще раздельные code paths).

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python | All | ✓ | 3.11+ (per CLAUDE.md) | — |
| PostgreSQL | All persistent state | ✓ | 16 (Docker container) | — |
| Telegram API | Onboarding, checker, listener | ✓ | TELEGRAM_API_ID/HASH env vars | Не функциональный без — но в v1 это всегда есть. |
| Encryption key | Session encryption | ✓ | ENCRYPTION_KEY env var | — |
| Supabase JWT secret | UI auth | ✓ (Phase 1) | SUPABASE_JWT_SECRET env var | — |
| pytest | Tests | ✓ | 8.0+ (Phase 1) | — |
| Docker Compose | Deploy | ✓ | Multi-container (api, listener, db) | — |
| Decodo proxy | (optional) | ⚠ (optional) | — | Telethon работает без proxy; BYO-proxy через UI/API. |
| `phonenumbers` lib | Phone normalization | ✗ | — | Regex-based normalizer в `app/utils/phone.py`. |
| `pandas` | CSV parsing | ✗ | — | `csv.DictReader` + `csv.Sniffer`. |
| Redis | (optional caching) | ✗ | — | НЕ нужен — PostgreSQL покрывает все state. |
| `apscheduler` / `celery` | (optional bg jobs) | ✗ | — | `asyncio.create_task` в lifespan. |
| `docker.sock` mount | (был для subprocess.run) | ⚠ | — | **УДАЛИТЬ** после Phase 2 D-18 — больше не требуется. |

**Missing dependencies with no fallback:** None.

**Missing dependencies with fallback:** Все опциональные, fallback на stdlib / regex.

## Validation Architecture

> nyquist_validation = true (в `.planning/config.json`, line 13).

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 8.0+ + pytest-asyncio 0.23+ (уже установлено Phase 1) |
| Config file | None найден (`pytest.ini` / `pyproject.toml` отсутствуют). Async-mode задаётся через `pytest_asyncio.fixture` явно. **Wave 0 plan может добавить `pyproject.toml` с `[tool.pytest.ini_options] asyncio_mode = "auto"` если planner сочтёт нужным — но это optional**. |
| Quick run command | `cd /Users/andrewbruce/Documents/outreach-platform && pytest tests/test_<module>.py -x -q` |
| Full suite command | `cd /Users/andrewbruce/Documents/outreach-platform && pytest tests/ -q` |
| Setup test DB | `docker compose up -d db` + `psql ... -c "CREATE DATABASE outreach_test"` (один раз). `conftest.py` сам создаёт схему через `Base.metadata.create_all + exec_driver_sql(012_*.sql)` для каждой test-session. Для Phase 2 расширить: + `exec_driver_sql(013_*.sql)`. |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| ONBD-01 | Phone + SMS-код вход | integration | `pytest tests/test_onboarding.py::test_start_then_verify_code_happy_path -x` | ❌ Wave 0 |
| ONBD-02 | 2FA после verify-code | integration | `pytest tests/test_onboarding.py::test_verify_code_with_2fa_flow -x` | ❌ Wave 0 |
| ONBD-03 | QR-вход | integration | `pytest tests/test_onboarding.py::test_qr_start_and_status -x` | ❌ Wave 0 |
| ONBD-04 | Аккаунт привязан к workspace | integration | `pytest tests/test_onboarding.py::test_created_sender_has_correct_workspace_id -x` | ❌ Wave 0 |
| ONBD-05 | Список со статусом | integration | `pytest tests/test_senders.py::test_list_senders_workspace_scoped -x` | ❌ Wave 0 |
| SNDR-01 | Rate limits с warning | integration | `pytest tests/test_senders.py::test_rate_limits_warnings_at_yellow_zone -x` + `::test_rate_limits_422_above_hard_cap` | ❌ Wave 0 |
| SNDR-02 | Per-account proxy | integration | `pytest tests/test_senders.py::test_assign_proxy_from_pool -x` | ❌ Wave 0 |
| SNDR-03 | Derived status | unit | `pytest tests/test_senders.py::test_derived_status_active_warmup_paused_error -x` | ❌ Wave 0 |
| CONT-01 | CSV-импорт | integration | `pytest tests/test_contacts.py::test_csv_import_preview_then_import -x` | ❌ Wave 0 |
| CONT-02 | Workspace isolation | integration | `pytest tests/test_contacts.py::test_list_contacts_workspace_scoped -x` | ❌ Wave 0 |
| CONT-03 | Push API через X-Workspace-Key | integration | `pytest tests/test_contacts.py::test_push_contacts_via_workspace_api_key -x` | ❌ Wave 0 |
| CONT-04 | TG-проверка | integration | `pytest tests/test_contact_check_worker.py::test_worker_tick_marks_pending_to_registered -x` (с mock'нутым CheckerService) | ❌ Wave 0 |
| CONT-05 | Поля контакта | unit | `pytest tests/test_contacts.py::test_contact_fields_and_custom_jsonb -x` | ❌ Wave 0 |
| FLDR-01 | Папки группируют контакты | integration | `pytest tests/test_folders.py::test_contact_belongs_to_one_folder -x` | ❌ Wave 0 |
| FLDR-02 | CRUD папок | integration | `pytest tests/test_folders.py::test_folder_crud_workspace_scoped -x` + `::test_delete_folder_with_contacts_returns_409` + `::test_delete_folder_force_cascade` | ❌ Wave 0 |
| FLDR-03 | folder_name auto-create | integration | `pytest tests/test_contacts.py::test_import_with_folder_name_creates_new_folder -x` | ❌ Wave 0 |

### Sampling Rate

- **Per task commit:** `pytest tests/test_<module>.py -x -q` (быстро, только релевантный модуль).
- **Per wave merge:** `pytest tests/ -q` (вся фаза).
- **Phase gate:** `pytest tests/ --tb=short && pytest tests/test_migration_013.py` (full + миграция smoke).

### Wave 0 Gaps

- [ ] `migrations/013_phase2.sql` — миграция (см. §"Code Examples").
- [ ] Расширение `tests/conftest.py` — factories `make_workspace`, `make_sender`, `make_checker`, `make_folder`, `make_contact`, `auth_headers_for_workspace`.
- [ ] `tests/test_migration_013.py` — smoke (все таблицы, индексы, CHECK constraints).
- [ ] `tests/test_phone_normalization.py` — `app/utils/phone.py` юнит-тесты (10+ edge cases).
- [ ] (Optional) `pyproject.toml` с `[tool.pytest.ini_options]` для `asyncio_mode="auto"` — облегчает писать тесты без декорирования каждой фикстуры.

### Test Strategy для Telethon-tied кода

`pytest` не может реально звонить в Telegram. Нужен **mock Telethon client**:

```python
# tests/conftest.py — добавить
@pytest_asyncio.fixture
def mock_telegram_client(monkeypatch):
    """Подменяет make_telegram_client на mock, возвращающий контролируемые ответы."""
    from unittest.mock import AsyncMock
    
    mock_client = AsyncMock()
    mock_client.connect = AsyncMock()
    mock_client.disconnect = AsyncMock()
    mock_client.is_connected = lambda: True
    mock_client.is_user_authorized = AsyncMock(return_value=True)
    # Stub send_code_request
    sent_code = AsyncMock()
    sent_code.phone_code_hash = "test_hash_123"
    mock_client.send_code_request = AsyncMock(return_value=sent_code)
    mock_client.sign_in = AsyncMock()
    mock_client.session.save = lambda: "mock_session_string"
    
    def _factory(*args, **kwargs):
        return mock_client
    
    monkeypatch.setattr("app.services.telegram.make_telegram_client", _factory)
    monkeypatch.setattr("app.routers.onboarding.make_telegram_client", _factory)
    return mock_client
```

Это позволит тестировать onboarding-flow ровно с тем же endpoint-payload-shape, что и в проде, без сети.

## Sources

### Primary (HIGH confidence)

- **Реальный код проекта** (HIGH — direct inspection):
  - `app/main.py:1-82` — состояние main.py после Phase 1, только health + workspace роутеры.
  - `app/utils/auth.py:1-246` — AuthDep, AuthCtx, dual-auth логика.
  - `app/routers/onboarding.py:1-776` — существующий onboarding flow (для рерайта).
  - `app/routers/senders.py:1-325` — существующий senders router, `subprocess.run(...)`, `is_active` использования.
  - `app/routers/check_contacts.py:1-126` — существующий check-contacts router.
  - `app/services/listener.py:1-1129` — listener с auto-reconnect, `get_active_senders`, signal handler.
  - `app/services/queue.py:1-200` — QueueWorker, rate-limit constants, working hours.
  - `app/services/telegram.py:1-716` — TelegramService, make_telegram_client, ResolvePhoneRequest.
  - `app/services/checker.py:1-249` — CheckerService (переиспользуется ContactCheckWorker'ом).
  - `app/models/__init__.py:1-344` — все ORM-модели.
  - `app/schemas/__init__.py:1-120` — Pydantic-конвенции.
  - `migrations/012_workspace.sql:1-135` — Phase 1 миграция (паттерн raw SQL).
  - `tests/conftest.py:1-90` — существующие фикстуры.
  - `requirements.txt:1-29` — все доступные deps.

- **CONTEXT.md решения** (HIGH — locked by user):
  - `.planning/phases/02-tg-accounts-contacts/02-CONTEXT.md:41-272` — D-01..D-22.
  - `.planning/phases/01-workspace-foundation/01-CONTEXT.md:17-167` — Phase 1 D-01..D-18 (особенно D-11/D-12 AuthDep, D-14 удаление старых роутеров).

- **Project meta** (HIGH):
  - `.planning/REQUIREMENTS.md:24-49` — Phase 2 requirements.
  - `.planning/codebase/ARCHITECTURE.md:91-115` — паттерн background workers.
  - `.planning/codebase/STRUCTURE.md:150-178` — конвенции расположения новых файлов.
  - `.planning/codebase/CONVENTIONS.md` — naming, error handling, logging patterns.
  - `CLAUDE.md` — все обязательные правила проекта.

### Secondary (MEDIUM confidence)

- **Telethon docs / Python knowledge from training** (MEDIUM — assistant training data, cutoff Jan 2026):
  - `TelegramClient.send_code_request()` → `SentCode` с `phone_code_hash` — стандартный flow.
  - `TelegramClient.sign_in(phone, code, phone_code_hash)` → может бросить `SessionPasswordNeededError` для 2FA.
  - `TelegramClient.qr_login()` → `QRLogin` с `.url` и async `.wait(timeout=...)`.
  - `client.session.save()` после `send_code_request` уже несёт DC-routing.
  - FloodWaitError на `sign_in` обычно 60-300 секунд, на `send_code_request` бывает в часах при злоупотреблении одного номера.
  - `csv.Sniffer().sniff(sample, delimiters=...)` — стандартный paragraph определения диалекта.

### Tertiary (LOW confidence)

- **Telethon-specific edge cases** (LOW — не verified против live Telegram API):
  - Точное поведение `qr_login.wait()` после рестарта процесса.
  - Точные FloodWait значения для `ResolvePhoneRequest` (зависит от account standing).
  - Recommendation: integration-test критичные flows на staging Telegram-аккаунте перед прод-деплоем.

## Metadata

**Confidence breakdown:**

- **Real code state**: HIGH — direct file inspection (CLAUDE.md, все 5 layers, 5 миграций, conftest).
- **CONTEXT.md decisions**: HIGH — verbatim copy.
- **Telethon-flow паттерны**: MEDIUM — основано на коде проекта + training data.
- **CSV-парсинг pitfalls**: HIGH — стандартные edge-cases стандартной библиотеки.
- **Wave organization**: HIGH — вытекает из dependency graph по реальным файлам.
- **Phone normalization heuristics**: MEDIUM — RU-centric допущение; international edge cases LOW.
- **Listener reconcile patterns**: MEDIUM — паттерн стандартный asyncio, но конкретное Telethon poxy-change поведение MEDIUM.
- **Validation Architecture**: HIGH — pytest-инфраструктура Phase 1 уже работает.

**Research date:** 2026-05-21
**Valid until:** ~2026-06-20 (30 дней — стабильные паттерны, единственный fast-moving источник — Telethon, версия 1.42 от Jan 2024 уже сама стабильна).
