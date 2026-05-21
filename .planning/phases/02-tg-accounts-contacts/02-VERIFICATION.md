---
phase: 02-tg-accounts-contacts
verified: 2026-05-21T19:00:00Z
status: gaps_found
score: 16/16 must-haves verified (artifact/wiring level) — 9 BLOCKER review findings remain unresolved
re_verification: null
gaps:
  - truth: "Multi-tenant inserts через worker'ы не нарушают NOT NULL constraints (workspace_id обязателен в messages_log, conversations, warmup_messages, warmup_sessions, context_contact_assignments после миграции 012)"
    status: failed
    reason: "CR-01..CR-04, CR-06 из 02-REVIEW.md не закрыты: queue.py, listener.py, warmup.py, rotation.py всё ещё пишут INSERT без workspace_id → первый успешный send/incoming/warmup-pair упадёт с NotNullViolation в multi-tenant БД"
    artifacts:
      - path: "app/services/queue.py:434, 652, 685-714, 762-797, 800-837"
        issue: "MessageLog(...), INSERT INTO conversations, MessageQueue(...) — все без workspace_id"
      - path: "app/services/listener.py:407-414, 449-460"
        issue: "INSERT INTO conversations, INSERT INTO messages без workspace_id"
      - path: "app/services/warmup.py:327-338, 421-434, 528-534"
        issue: "INSERT INTO warmup_messages, warmup_sessions без workspace_id; SQL precedence bug в UPDATE"
      - path: "app/services/rotation.py:96-103"
        issue: "INSERT INTO context_contact_assignments без workspace_id; _pick_best_sender без workspace-фильтра"
    missing:
      - "Пробросить workspace_id в queue.enqueue_message/enqueue_file и _send_item.MessageLog(...)"
      - "Пробросить workspace_id из senders в listener get_or_create_conversation INSERT"
      - "Пробросить workspace_id в warmup INSERT'ы; обернуть OR/AND в скобки в _process_session"
      - "Партиционировать warmup._get_active_pool по workspace_id (CR-04 issue 3 — cross-tenant pair leak)"
      - "Пробросить workspace_id в rotation INSERT/SELECT; добавить workspace guard в _pick_best_sender"
  - truth: "Reauth flow для existing sender обновляет session_string, а не создаёт нового sender'а"
    status: failed
    reason: "CR-05: /reauth/{sender_slug} → verify-code → _create_sender_from_session делает INSERT с slug=sender-{telegram_id}; так как Sender.slug globally unique (unique=True), второй reauth тех же telegram_id даст IntegrityError. Реальная цель reauth — UPDATE existing sender — не реализована"
    artifacts:
      - path: "app/routers/onboarding.py:447, 502, 616"
        issue: "После reauth verify-code/2fa/qr вызывается _create_sender_from_session — нет ветки UPDATE для существующего sender_id"
    missing:
      - "Добавить _refresh_sender_session helper или ветку 'if session_row.is_reauth: UPDATE sender_id ELSE create' в verify-code"
      - "Хранить original_sender_id в onboarding_sessions при reauth/{sender_slug} (добавить колонку или использовать другой признак)"
      - "Покрыть reauth integration-тестом в tests/test_onboarding.py"
  - truth: "/api/v1/health не раскрывает агрегаты sender'ов всех workspace'ов анонимно"
    status: failed
    reason: "CR-07: endpoint без Depends(auth_dep) делает select(Sender) без workspace-фильтра и возвращает total/active по всем tenant'ам — information disclosure уровня multi-tenant SaaS"
    artifacts:
      - path: "app/routers/health.py:34-42"
        issue: "select(Sender).all() без workspace_id фильтра; endpoint без auth_dep"
    missing:
      - "Убрать senders-stats из публичного /health (минимальный fix: только db_status + version + uptime)"
      - "Если нужны детали — отдельный /health/detailed под Depends(auth_dep) с workspace-scope"
  - truth: "ContactCheckWorker безопасен под горизонтальным масштабированием API"
    status: partial
    reason: "CR-08: SELECT pending контактов без FOR UPDATE SKIP LOCKED — два worker'а могут взять одни и те же rows, оба вызовут CheckerService (lock на checker_slug спасает Telethon, но не дублирующий UPDATE и расход rate-limit). Сейчас v1 single-container = не блокирует, но архитектурный риск"
    artifacts:
      - path: "app/services/contact_check_worker.py:104-130"
        issue: "SELECT contacts JOIN LATERAL ... LIMIT :n без FOR UPDATE SKIP LOCKED"
    missing:
      - "Либо добавить FOR UPDATE OF c SKIP LOCKED, либо помечать contacts.tg_status='processing' атомарно (как делает queue.py для message_queue)"
  - truth: "AuthCtx API key verification защищён от timing attack + не тратит CPU bcrypt без rate-limit"
    status: partial
    reason: "CR-09: _verify_api_key итерирует по candidates и зовёт bcrypt.checkpw (~100ms каждый). Нет cache validated tokens; нет rate-limit middleware; last_used_at = func.now() — анти-паттерн (SQL-expression в Python-поле)"
    artifacts:
      - path: "app/utils/auth.py:191-246"
        issue: "bcrypt без LRU/TTL cache, без rate-limit; last_used_at = func.now() вместо datetime.now(timezone.utc)"
    missing:
      - "In-process LRU cache validated tokens с 5-min TTL (особенно критично для CONT-03 push: n8n может слать тысячи запросов)"
      - "Использовать hmac.compare_digest для constant-time prefix lookup"
      - "Заменить last_used_at = func.now() на datetime.now(timezone.utc)"
human_verification:
  - test: "Запустить очередь сообщений в реальном multi-tenant БД"
    expected: "queue.py создаёт message_queue/messages_log/conversations записи с правильным workspace_id; при отсутствии — NotNullViolation"
    why_human: "Требует поднятого Postgres с миграциями 012+013 и реальный send → проверка только под нагрузкой/интеграционный сценарий"
  - test: "Проверить полный onboarding flow с реальным Telegram-аккаунтом (phone → SMS → 2FA)"
    expected: "Аккаунт создаётся в workspace, listener reconcile подхватывает его в течение 30 сек, статус active/error отражается в UI"
    why_human: "Telethon-сторона требует реального SMS — мокать невозможно; reconcile-loop тестируется только end-to-end с реальным БД"
  - test: "Проверить CSV-импорт с реальным файлом из Russian Excel (BOM + CP1251 + ; delimiter)"
    expected: "parse_preview корректно detect'ит encoding/delimiter; apply_import создаёт contacts с tg_status='pending' или 'unchecked'"
    why_human: "Edge cases CSV (encoding fallback, BOM, dialects) сложно эмулировать в unit-тестах — требуется реальный файл от потенциального клиента"
  - test: "Проверить что ContactCheckWorker действительно обновляет tg_status через checker"
    expected: "Контакт с phone='+79001234567' проходит pending → registered (если в TG) или not_registered; tg_telegram_id заполняется"
    why_human: "Требует реального checker-аккаунта (отдельная Telegram-сессия) и реального запроса ResolvePhone в Telegram MTProto"
  - test: "Проверить /reauth flow для existing sender (UPDATE а не INSERT)"
    expected: "Reauth существующего sender'а заменяет его session_string и НЕ создаёт второго sender'а в БД"
    why_human: "Сейчас по коду (CR-05) этот flow сломан — нужно подтвердить визуально что в integration-тесте/dev-окружении ситуация именно такая; и проверить fix после исправления"
---

# Phase 2: TG Accounts & Contacts — Verification Report

**Phase Goal (из ROADMAP.md):** Клиент подключает свои Telegram-аккаунты в workspace, настраивает их (rate limits, прокси), загружает базу контактов с папками и проверяет наличие в Telegram при импорте.

**Verified:** 2026-05-21T19:00:00Z
**Status:** gaps_found
**Re-verification:** No — initial verification.

---

## Резюме (RU)

Phase 02 на уровне **заявленной фичи** (16 требований ONBD/SNDR/CONT/FLDR) — выполнена: миграция 013 применена, все 5 роутеров переписаны workspace-scoped через `Depends(auth_dep)`, ContactCheckWorker и OnboardingCleanupWorker подключены в lifespan, listener reconcile-loop заменил `subprocess.run('docker restart')`, CSV-импорт с двух-шаговым flow и dedup через `ON CONFLICT` работает.

Однако `02-REVIEW.md` нашёл **9 BLOCKER**-уровня проблем, ни одна из которых не закрыта в коде на момент верификации. Большинство из них — наследие старых worker'ов (queue/listener/warmup/rotation), которые Phase 02 не должна была переписывать, но они теперь стали блокером работоспособности в multi-tenant БД (миграция 012 сделала `workspace_id` обязательным для `messages_log`, `conversations`, `warmup_*`, `context_contact_assignments`, а INSERT'ы не пробрасывают это поле).

**Goal-backward вывод:** феча "клиент подключает аккаунт, импортирует контакты, проверяет через checker" — собрана. Но как только клиент попытается **отправить первое сообщение** через эту инфраструктуру (это уже Phase 4, но queue.py существует и работает в текущем коде) — упадёт `NotNullViolation`. То есть Phase 02 само-по-себе не блокирует *использование* собранных features (онбординг + импорт + проверка), но создаёт latent блокер для следующей фазы.

Рекомендация: **открыть отдельный фикс-план 02.1 на 9 BLOCKER** перед стартом Phase 03/04. Альтернатива (более дорогая) — отметить Phase 02 как complete с известными gaps, а CR-01..CR-04 закрыть в первом плане Phase 04 (queue rewrite for campaign_id) — там queue.py всё равно будет правиться.

---

## Goal Achievement

### Observable Truths (Success Criteria из ROADMAP)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Пользователь проходит онбординг TG-аккаунта (телефон → SMS → готово), поддерживается 2FA и QR; аккаунт привязан к workspace | ✓ VERIFIED | `app/routers/onboarding.py` — 8 endpoint'ов под `Depends(auth_dep)`; persistent state в `onboarding_sessions`; recovery через `decrypt_session`; ONBD-01..05 закрыты по 02-01-SUMMARY |
| 2 | На странице аккаунта пользователь задаёт rate limits (с warning при выходе за «зелёный коридор» 4/20/150) и прокси | ✓ VERIFIED | `app/routers/senders.py:60` RATE_HARD_CAP={10,50,300}, RATE_SOFT_CAP={4,20,150}; PATCH возвращает 200+warnings или 422; POST /senders/{slug}/assign-proxy; ProxyPool workspace-scoped (D-22) |
| 3 | Список аккаунтов workspace показывает live-статус каждого (активен / прогрев / пауза / ошибка) | ✓ VERIFIED | `app/routers/senders.py:68 _derive_status` — error if auth_status != 'ok', else lifecycle_status; миграция 013 добавила lifecycle_status + дропнула is_active; listener reconcile-loop подключает/отключает по lifecycle |
| 4 | Пользователь загружает CSV в выбранную папку — телефоны проверяются в TG через checker, статус сохраняется | ✓ VERIFIED | `app/routers/contacts.py` 2-step CSV (preview→apply); `app/services/contact_check_worker.py` JOIN LATERAL workspace-isolated; D-20 has_checker fallback (tg_status='unchecked'); `app/routers/check_contacts.py` /recheck |
| 5 | Папки CRUD: создание / переименование / удаление; контакты можно перемещать между папками | ✓ VERIFIED | `app/routers/folders.py` — list/create/get/rename/delete + ?force=true cascade + 409 FOLDER_NOT_EMPTY с {contact_count,active_campaigns}; `app/routers/contacts.py` /move single + batch |

**Score:** 5/5 success-criteria достигнуты на уровне поверхности.

---

## Must-Haves по планам

### Plan 02-01: Onboarding rewrite + listener reconcile

| Must-have (truth/artifact/key_link) | Status | Evidence |
|---|---|---|
| Юзер запускает онбординг через POST /onboarding/start → SMS, возвращается session_id | ✓ | onboarding.py:280+, save_state, _in_process_clients |
| verify-code создаёт sender в workspace юзера | ✓ | onboarding.py:447 `_create_sender_from_session(... workspace_id=ctx.workspace_id ...)` |
| verify-2fa обрабатывает 2FA после verify-code | ✓ | onboarding.py:502 ветка SessionPasswordNeededError → awaiting_2fa → verify-2fa |
| QR-вход через qr-start + qr-status polling | ✓ | onboarding.py qr-start/qr-finish/qr-status; QR base64 PNG |
| onboarding_sessions table — рестарт api не теряет phone_code_hash | ✓ | save_state шифрует session_string + phone_code_hash в БД; _get_or_recover_client decrypt'ит при cache miss |
| Listener.reconcile_loop каждые 30 сек | ✓ | listener.py:1194 `_reconcile_loop`, LISTENER_RECONCILE_INTERVAL env var, 1137 _reconcile_tick |
| subprocess.run('docker restart') полностью удалён | ✓ | grep `subprocess` по routers/onboarding.py + senders.py = 0; docker.sock из compose удалён |
| `docker.sock` mount удалён из api service | ✓ | `grep -c "docker.sock" docker-compose.yml` = 0 |

**Verdict:** все 8 truths VERIFIED.

### Plan 02-02: Migration 013 + senders router + rate-limit warnings

| Must-have | Status | Evidence |
|---|---|---|
| Миграция 013 создаёт folders/contacts/onboarding_sessions/csv_imports + расширяет senders + дропает is_active | ✓ | `migrations/013_phase2.sql` 119 строк; все 4 CREATE TABLE IF NOT EXISTS; senders DROP COLUMN IF EXISTS is_active; CHECK constraints на role/lifecycle_status/tg_status/onboarding_sessions.status |
| Юзер задаёт rate limits per-sender; 4/20/150 — defaults, до 10/50/300 — warning, выше — 422 | ✓ | senders.py:60 RATE_HARD_CAP/SOFT_CAP, _validate_rate_limits бросает 422 при превышении; tests/test_senders.py покрывает оба сценария |
| API GET /senders/{id} возвращает derived status | ✓ | senders.py:68 _derive_status; SenderResponse.status: Literal['active','warmup','paused','error'] |
| POST /senders/{id}/assign-proxy | ✓ | senders.py:395 assign-proxy endpoint; ProxyPool.workspace_id filter |
| queue.py читает sender.rate_per_min/hour/day | ✓ | queue.py больше не содержит MAX_MSGS_PER_*; читает из sender row per-tick |
| Listener и warmup-worker фильтруют по lifecycle_status='active' AND auth_status='ok' | ✓ | listener.py get_active_senders + warmup _get_active_pool — все is_active заменены |
| tests/conftest.py имеет фабрики Phase 2 | ✓ | conftest.py:112 test_workspace, 122 test_sender_factory, 157 test_checker, 163 test_folder, 173 test_contacts_factory |

**Verdict:** все 7 truths VERIFIED.

### Plan 02-03: Folders CRUD

| Must-have | Status | Evidence |
|---|---|---|
| Workspace-scoped CRUD папок (5 endpoint'ов) | ✓ | folders.py: list/create/get/rename/delete, все через Depends(auth_dep) |
| 409 FOLDER_NOT_EMPTY с {contact_count, active_campaigns:[]} при удалении непустой | ✓ | folders.py:238-247 |
| ?force=true каскад через FK | ✓ | folders.py:209 force=Query, FK ondelete=CASCADE в миграции |
| get_or_create_by_name helper для FLDR-03 | ✓ | folders.py:38 export'ится; используется в contacts.py |
| Cross-tenant изоляция: 404 FOLDER_NOT_FOUND | ✓ | folders.py:111,144,168,187,221 — все SELECT с workspace_id фильтром |

**Verdict:** все 5 truths VERIFIED.

### Plan 02-04: Contacts + CSV import + push API

| Must-have | Status | Evidence |
|---|---|---|
| Contacts API: list/push/preview/import/move/delete | ✓ | contacts.py 7 endpoint'ов; UploadFile multipart для preview |
| Phone normalization E.164 (без phonenumbers lib) | ✓ | app/utils/phone.py — pure regex, RU heuristic; 15 тестов в test_phone_normalization.py |
| CSV import: parse_preview + suggest_mapping + apply_import (без pandas) | ✓ | app/services/csv_import.py — stdlib csv; BOM/cp1251/semicolon fallback; 20 тестов |
| FLDR-03 folder_name auto-create on push and CSV import | ✓ | _resolve_folder_id → get_or_create_by_name reuse |
| D-19 async pipeline: tg_status='pending' для worker | ✓ | _insert_contacts_with_dedup() ставит tg_status в зависимости от has_checker |
| D-20 has_checker fallback: tg_status='unchecked' если нет checker'а | ✓ | _has_checker(workspace_id) — SELECT COUNT senders WHERE role='checker'; default_tg_status branch |
| Dedup через partial UNIQUE + ON CONFLICT DO NOTHING | ✓ | миграция 013 partial UNIQUE indexes; ON CONFLICT DO NOTHING RETURNING id |

**Verdict:** все 7 truths VERIFIED.

### Plan 02-05: ContactCheckWorker + recheck + has_checker

| Must-have | Status | Evidence |
|---|---|---|
| ContactCheckWorker async background task | ✓ | contact_check_worker.py:47 class + module-level singleton; main.py:47 start/54 stop |
| JOIN LATERAL workspace-isolated SQL | ✓ | contact_check_worker.py:115-124 JOIN LATERAL WHERE workspace_id = c.workspace_id |
| POST /api/v1/contacts/recheck | ✓ | check_contacts.py:41 endpoint, 202 Accepted, RecheckRequest with contact_ids or folder_id |
| has_checker на GET /api/v1/workspace | ✓ | workspace.py:56 WorkspaceResponse.has_checker; _workspace_has_checker helper |
| Reuse CheckerService.check_phones (no duplication) | ✓ | contact_check_worker.py:148 await checker_service.check_phones(...) |
| Cross-tenant UPDATE filter (workspace_id) | ✓ | check_contacts.py recheck — WHERE workspace_id = :wid |

**Verdict:** все 6 truths VERIFIED.

---

## Required Artifacts (cross-check)

| Artifact | Expected | Status | Details |
|---|---|---|---|
| `migrations/013_phase2.sql` | 4 CREATE TABLE + senders ALTER + is_active DROP | ✓ VERIFIED | 119 lines, BEGIN/COMMIT, idempotent |
| `app/models/__init__.py` | Folder, Contact, OnboardingSession, CsvImport + Sender extended | ✓ VERIFIED | классы на строках 352/365/398/421; Sender.lifecycle_status/rate_per_*; is_active отсутствует на Sender |
| `app/schemas/__init__.py` | FolderResponse, ContactCreate, SenderResponse(derived), WarningItem, RecheckRequest, ContactImport* | ✓ VERIFIED (по 02-02-SUMMARY) | Все классы добавлены; SenderResponse.status: Literal |
| `app/routers/folders.py` | CRUD + get_or_create_by_name helper | ✓ VERIFIED | 5 endpoints + helper экспортируется |
| `app/routers/contacts.py` | 7 endpoints (list/push/preview/import/move/move-batch/delete) | ✓ VERIFIED | 8× Depends(auth_dep), 4× Contact.workspace_id ==, UploadFile |
| `app/routers/senders.py` | CRUD + assign-proxy + warnings + derived status | ✓ VERIFIED | _derive_status, RATE_HARD_CAP, no subprocess, no is_active |
| `app/routers/onboarding.py` | 8 endpoints workspace-scoped + persistent state | ✓ VERIFIED | _in_process_clients, _get_or_recover_client, decrypt_session; no _onboarding_sessions:dict, no subprocess |
| `app/routers/check_contacts.py` | /recheck endpoint workspace-scoped | ✓ VERIFIED | Depends(auth_dep), 202 Accepted, RecheckRequest |
| `app/services/contact_check_worker.py` | Class + module singleton + JOIN LATERAL SQL | ✓ VERIFIED | class ContactCheckWorker, lifespan-registered |
| `app/services/csv_import.py` | parse_preview / suggest_mapping / apply_import | ✓ VERIFIED | stdlib csv; encoding fallback chain |
| `app/services/onboarding_state.py` | save_state/load_state/update_status + cleanup worker | ✓ VERIFIED | OnboardingCleanupWorker + module singleton |
| `app/utils/phone.py` | normalize_to_e164 | ✓ VERIFIED | pure regex E.164 + RU heuristic |
| `docker-compose.yml` | docker.sock mount removed | ✓ VERIFIED | grep returns 0 |
| `tests/conftest.py` | Phase 2 fixtures | ✓ VERIFIED | 5 fixtures present |

---

## Key Link Verification

| From | To | Via | Status | Details |
|---|---|---|---|---|
| `app/main.py` | senders/folders/contacts/check_contacts/onboarding routers | `app.include_router(...)` | ✓ WIRED | main.py:81-87 — все 7 роутеров зарегистрированы |
| `app/main.py` | onboarding_cleanup_worker + contact_check_worker | lifespan start/stop | ✓ WIRED | main.py:45-48 start, 54-55 stop |
| `app/routers/onboarding.py` | `OnboardingSession` ORM | INSERT/SELECT/UPDATE через save_state/load_state | ✓ WIRED | save_state в onboarding_state.py uses `db.add(OnboardingSession(...))` |
| `app/services/listener.py::_reconcile_loop` | senders.lifecycle_status + auth_status | periodic SELECT через get_active_senders | ✓ WIRED | listener.py:1137-1230, SQL фильтрует role='sender' AND lifecycle_status='active' AND auth_status='ok' |
| `app/routers/contacts.py` | `get_or_create_by_name` from folders.py | import + _resolve_folder_id | ✓ WIRED | contacts.py импортирует helper, использует в push и CSV apply |
| `app/services/contact_check_worker.py` | `checker_service.check_phones` | reuse existing | ✓ WIRED | contact_check_worker.py:148 |
| `app/routers/workspace.py` | senders (role='checker') | _workspace_has_checker COUNT | ✓ WIRED | workspace.py:104; вызов в GET и PATCH |
| `app/routers/contacts.py` | `csv_import` service + `phone.normalize_to_e164` | imports | ✓ WIRED | в preview/import flows |

---

## Data-Flow Trace (Level 4)

Phase 02 — API/worker слой без UI-рендера, так что классический "props hardcoded empty" не применим. Главные data-flow:

| Artifact | Data | Source | Real Data Flow | Status |
|---|---|---|---|---|
| `GET /senders` response | sender rows + derived status | DB SELECT через ORM | ✓ Реальный workspace-scoped SELECT с computed `_derive_status` | ✓ FLOWING |
| `POST /contacts/import` → contacts | rows from CSV blob | csv_imports BYTEA + applied mapping → INSERT contacts | ✓ Реальная цепочка blob→parse→normalize→ON CONFLICT INSERT | ✓ FLOWING |
| `ContactCheckWorker._tick` → contacts UPDATE | summary['results'] из checker_service.check_phones | реальный Telethon ResolvePhone | ✓ Реальный API-вызов в Telegram MTProto | ✓ FLOWING (но требует human verification — Telethon mock в тестах) |
| `GET /workspace.has_checker` | COUNT senders WHERE role='checker' | DB query | ✓ Real COUNT | ✓ FLOWING |
| `Listener._reconcile_tick` → connect/disconnect | desired vs current set diff | DB SELECT каждые 30 сек | ✓ Real periodic SELECT + asyncio.create_task connect | ✓ FLOWING |

Нет hollow/static fallback'ов на путях data-flow Phase 02.

---

## Behavioral Spot-Checks

Запуск автоматических spot-check'ов невозможен без поднятого Postgres + venv с Telethon. Phase 02 — async-API слой; основные behaviour'ы тестируются интеграционно (test_senders, test_contacts, test_folders, test_onboarding, test_contact_check_worker, test_check_contacts).

**Skip-причина:** "no runnable entry points в текущем окружении (нет venv с зависимостями + Postgres)". По 02-04-SUMMARY и 02-05-SUMMARY все тесты собирают (`pytest --collect-only`) и unit-часть (phone normalization, csv_import) проходит локально.

---

## Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|---|---|---|---|---|
| ONBD-01 | 02-01 | Добавление TG-аккаунта через телефон + SMS | ✓ SATISFIED | onboarding.py /start + /verify-code |
| ONBD-02 | 02-01 | 2FA | ✓ SATISFIED | onboarding.py /verify-2fa + SessionPasswordNeededError branch |
| ONBD-03 | 02-01 | QR-вход | ✓ SATISFIED | onboarding.py /qr-start + /qr-status + _wait_for_qr |
| ONBD-04 | 02-01 | Аккаунт привязан к workspace | ✓ SATISFIED | _create_sender_from_session(... workspace_id=ctx.workspace_id ...) |
| ONBD-05 | 02-01 | Список аккаунтов со статусом | ✓ SATISFIED | senders.py GET /senders + derived status |
| SNDR-01 | 02-02 | Per-account rate limits + warning | ✓ SATISFIED | RATE_HARD_CAP/SOFT_CAP в senders.py; warnings[] в response |
| SNDR-02 | 02-02 | Per-account прокси + workspace pool | ✓ SATISFIED | POST /senders/{slug}/assign-proxy; ProxyPool CRUD |
| SNDR-03 | 02-02 | Статус аккаунта (active/warmup/paused/error) | ✓ SATISFIED | derived status; lifecycle_status + auth_status raw fields |
| CONT-01 | 02-04 | CSV-импорт | ✓ SATISFIED | 2-step preview→import; multipart UploadFile |
| CONT-02 | 02-04 | Контакты привязаны к workspace | ✓ SATISFIED | contacts.workspace_id NOT NULL FK; все SELECT с workspace_id |
| CONT-03 | 02-04 | Push через Workspace API | ✓ SATISFIED | POST /api/v1/contacts + Depends(auth_dep) (X-Workspace-Key path) |
| CONT-04 | 02-05 | Проверка наличия в TG через checker | ✓ SATISFIED | ContactCheckWorker + JOIN LATERAL + recheck endpoint |
| CONT-05 | 02-04 | Поля контакта (phone/username/full_name/source/custom JSONB) | ✓ SATISFIED | модель Contact + миграция 013 |
| FLDR-01 | 02-03 | Контакты группируются по папкам, 1 контакт = 1 папка | ✓ SATISFIED | contacts.folder_id NOT NULL FK; move endpoint |
| FLDR-02 | 02-03 | CRUD папок + запрет удаления непустой | ✓ SATISFIED | folders.py + 409 FOLDER_NOT_EMPTY + force |
| FLDR-03 | 02-04 | При импорте — выбор папки (auto-create если нет) | ✓ SATISFIED | get_or_create_by_name reuse в push + CSV import |

**16/16 requirements SATISFIED** на feature-level. Все 16 закрыты по артефактам и wiring; runtime поведение для нескольких требуется подтвердить human-тестами (см. human_verification frontmatter).

---

## Code-Review Delta (02-REVIEW.md → реальный код)

REVIEW.md был сгенерирован после завершения всех 5 планов и нашёл 9 BLOCKER + 11 WARNING + 6 INFO findings. На момент верификации **0 из 9 BLOCKER** закрыты в коде:

| ID | Issue | Verified in code (2026-05-21) | Closed? |
|---|---|---|---|
| CR-01 | queue.py INSERT MessageLog/conversations без workspace_id | `grep MessageLog app/services/queue.py:434,652` — workspace_id отсутствует | ✗ OPEN |
| CR-02 | listener.py INSERT conversations без workspace_id | `listener.py:407-414` INSERT без workspace_id | ✗ OPEN |
| CR-03 | rotation.py INSERT context_contact_assignments без workspace_id; _pick_best_sender без guard | `rotation.py:96-103, 132-164` — нет workspace_id | ✗ OPEN |
| CR-04 | warmup.py все INSERT без workspace_id; SQL precedence bug; cross-tenant pair leak в _get_active_pool | `warmup.py:327-338, 421-434, 528-534` — все три issue в силе | ✗ OPEN |
| CR-05 | Reauth flow всегда создаёт нового sender'а (slug global unique → IntegrityError на втором reauth) | onboarding.py:447,502,616 — все три ветки вызывают `_create_sender_from_session` (INSERT) без UPDATE-варианта | ✗ OPEN |
| CR-06 | Worker'ы (queue/warmup/rotation) — workspace_id отсутствует во всех SELECT/UPDATE | подтверждается grep'ом `grep -rn "workspace_id" app/services/{queue,warmup,rotation}.py` → 0 matches | ✗ OPEN |
| CR-07 | /api/v1/health показывает sender-counts всех workspaces без auth | health.py:34-42 — `select(Sender).all()` без auth, без workspace filter | ✗ OPEN |
| CR-08 | ContactCheckWorker SELECT без FOR UPDATE SKIP LOCKED — race при горизонтальном масштабе | contact_check_worker.py:104-130 — без SKIP LOCKED | ✗ OPEN |
| CR-09 | AuthCtx _verify_api_key без timing-attack защиты, без LRU cache, last_used_at=func.now() | auth.py:191-246 — bcrypt в loop, без cache | ✗ OPEN |

**Delta:** 0/9 closed. Это ожидаемо — REVIEW сгенерирован вместе с верификацией, и эти проблемы не входили в scope планов 02-01..02-05 (они частично — наследие старых worker'ов после миграции 012). Однако они подняты ровно перед закрытием фазы и должны быть учтены.

### Warnings (11) и Info (6) — статус

Не проверял каждый WR/IN отдельно; spot-check:
- **WR-01** (Sender.telegram_id отсутствует в ORM): подтверждается — на Sender модели нет `telegram_id` колонки, хотя миграция 006 её добавляет в БД. ✗ OPEN.
- **WR-02** (Sender.slug global unique — нарушение multi-tenant изоляции): `slug = Column(String(50), unique=True)` в models/__init__.py:80. ✗ OPEN.
- **WR-07** (Phone normalization дублируется: `_normalize_phone` в onboarding.py vs `normalize_to_e164` в utils/phone.py): `_normalize_phone` всё ещё в onboarding.py:116, используется в строках 308 и 711. ✗ OPEN.

Остальные WR/IN — менее критичны, не блокируют Phase 02 closure.

---

## Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|---|---|---|---|---|
| `app/services/queue.py` | 434, 652, 685+ | INSERT без workspace_id | 🛑 Blocker | NotNullViolation в multi-tenant БД (Phase 4+ блокер) |
| `app/services/listener.py` | 407 | INSERT INTO conversations без workspace_id | 🛑 Blocker | AI-listener не сможет создать диалог |
| `app/services/warmup.py` | 528-534 | `WHERE A OR B AND C` без скобок | 🛑 Blocker | SQL precedence bug — обновляет завершённые сессии |
| `app/services/warmup.py` | 167-176 | _get_active_pool без workspace partitioning | 🛑 Blocker | Cross-tenant warmup pairs — leak senders между workspace'ами |
| `app/routers/onboarding.py` | 447, 502, 616 | Reauth → _create_sender_from_session (INSERT) | 🛑 Blocker | IntegrityError на втором reauth — UX broken |
| `app/routers/health.py` | 34-42 | select(Sender) без auth, без workspace filter | 🛑 Blocker | Information disclosure (multi-tenant SaaS) |
| `app/models/__init__.py` | 80 | Sender.slug unique=True (globally) | ⚠️ Warning | UNIQUE conflict с другого workspace; info disclosure |
| `app/routers/onboarding.py` | 116 | _normalize_phone дублирует normalize_to_e164 | ⚠️ Warning | Inconsistency между onboarding и CSV import phone validation |
| `app/utils/auth.py` | 222 | last_used_at = func.now() (SQL-expression в Python attr) | ⚠️ Warning | Анти-pattern; работает только благодаря SQLAlchemy лояльности |
| `app/services/contact_check_worker.py` | 104 | SELECT без FOR UPDATE SKIP LOCKED | ⚠️ Warning | Race condition при горизонтальном масштабе (v2) |
| `app/utils/auth.py` | 191-246 | bcrypt в loop без LRU cache | ⚠️ Warning | CPU bottleneck для high-throughput n8n push |

---

## Recommendations

### Вариант A (рекомендую): Открыть отдельную фазу 02.1 — "Multi-tenant worker hardening"

**Перед стартом Phase 03 (Agents).** Закрыть как минимум CR-01..CR-04 + CR-07. Это критически важно потому что:

1. **CR-01 (queue.py NotNullViolation)** сломает Phase 04 (queue rewrite for campaign_id) — там queue.py всё равно будет правиться, но если уже сейчас зафиксировать workspace_id в INSERT'ах, Phase 04 будет проще.
2. **CR-02 (listener.py NotNullViolation)** уже сейчас может ломать AI-listener в multi-tenant БД. Если у Phase 02 был бы интеграционный тест с реальным incoming → AI reply, он бы упал.
3. **CR-04 (warmup cross-tenant pair leak)** — security-issue: sender из workspace A пишет sender'у из workspace B через warmup. Это must-fix перед первым внешним клиентом.
4. **CR-05 (reauth)** — UX-блокер для ONBD requirements'ов: юзер не сможет переавторизовать аккаунт после session_expired.
5. **CR-07 (/health information disclosure)** — security; одна строка фикса.

Объём фикс-плана 02.1: ~2-3 task'а, ~3-4 часа работы. Состав:
- Task 1: Workspace_id sweep по queue.py / listener.py / warmup.py / rotation.py — пробросить в INSERT'ы; CR-04 issue 3 (warmup partitioning) — отдельный sub-task.
- Task 2: Reauth flow — добавить `_refresh_sender_session` ветку; CR-05 fix + integration test.
- Task 3: /health lockdown; Sender.slug UNIQUE (workspace_id, slug) миграция 014; WR-07 phone normalization consolidate.

### Вариант B (более дорогой): Закрыть Phase 02 с known issues; CR-* решать вместе с Phase 04

Если Phase 03 (Agents) не использует queue/listener/warmup в новом коде, можно отложить CR-01..CR-04 до Phase 04 first plan ("Audit existing webhook + function calling"). В этом случае:
- Обязательно зафиксировать CR-05 и CR-07 в Phase 02.1 micro-fix (security/UX блокеры)
- CR-08, CR-09, WR-* — пере-катить в Phase 04 / Phase 05.

### Вариант C (НЕ рекомендую): Закрыть Phase 02 как-есть и игнорировать REVIEW

Это оставит multi-tenant security дыры (CR-04, CR-07) на боевой системе. Запрещается.

---

## Status

**gaps_found** — Phase 02 на уровне фичи (16 requirements) собрана и работает на surface level; но code review нашёл 9 BLOCKER, ни один из которых не закрыт. Рекомендую Вариант A: отдельный фикс-план 02.1 перед Phase 03.

**Score:** 16/16 requirements satisfied (artifact + wiring), **0/9 BLOCKER findings remediated**.

---

_Verified: 2026-05-21T19:00:00Z_
_Verifier: Claude (gsd-verifier)_
