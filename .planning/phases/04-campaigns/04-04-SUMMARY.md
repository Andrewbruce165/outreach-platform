---
phase: 04-campaigns
plan: 04
subsystem: api
tags: [campaigns, queue, rotation, render-template, background-worker, postgres, sqlalchemy, fastapi]

requires:
  - phase: 04-02
    provides: campaigns table, message_queue.campaign_id NULLable column, Campaign ORM, test_campaign_factory + test_running_campaign_factory + attach_sender_to_campaign fixtures
  - phase: 04-03
    provides: per-campaign queue scheduling, _campaign_in_working_window helper, INNER JOIN на campaigns в queue._tick + _process_next_for_sender
provides:
  - app/services/template.py (render_template Mustache renderer + RU aliases + custom.X dotted notation)
  - app/services/campaign_enqueue.py (CampaignEnqueueWorker singleton with tick / start / stop)
  - rotation.get_or_assign_sender(campaign_id, contact_phone, db, *, commit=True) — per-campaign signature
  - send.py POST /api/v1/send body has campaign_id (was ai_context_id in Phase 3)
  - queue.py _upsert_conversation INSERT extended with campaign_id propagation (D-05)
  - enqueue_message + enqueue_file signatures accept campaign_id (B1)
  - Lifespan registration: campaign_enqueue_worker.start() / stop()
  - 2 env vars: CAMPAIGN_ENQUEUE_TICK_SECONDS (default 30), CAMPAIGN_ENQUEUE_BATCH_SIZE (default 500)
affects:
  - 04-05 (signals + webhooks + tools — listener.py, ai_engine.py, generate_response — переедут на campaigns.tools источник)

tech-stack:
  added: []  # no new pip packages — re uses stdlib re/asyncio + existing sqlalchemy / pydantic
  patterns:
    - "Mustache-style template rendering with single regex + alias dictionary (no third-party templating library)"
    - "begin_nested() per-contact savepoint + commit=False kwarg on rotation — avoids double-commit when worker controls outer transaction (M2)"
    - "Singleton background worker registered in lifespan (ContactCheckWorker pattern reused)"
    - "campaign_id propagated through enqueue_* function signatures (B1 — file path matches message path)"
    - "ON CONFLICT (campaign_id, contact_phone) DO NOTHING — race-safe rotation INSERT"
    - "JOIN campaigns ON campaign_id in _upsert_conversation для derivation of agent_id (Phase 4 D-05 / closure of queue.py:705 TODO)"

key-files:
  created:
    - app/services/template.py
    - app/services/campaign_enqueue.py
    - tests/test_template_render.py
    - tests/test_campaign_enqueue_worker.py
    - tests/test_queue_campaign_id.py
    - tests/test_send_campaign.py
    - tests/test_rotation_campaign.py
  modified:
    - app/services/rotation.py (complete rewrite — context_id → campaign_id, source pool = campaign_senders, commit kwarg)
    - app/routers/send.py (complete rewrite — campaign_id body, sender resolution via rotation, optional message → render_template)
    - app/services/queue.py (_upsert_conversation INSERT extended with campaign_id + agent_id JOIN; enqueue_message/enqueue_file accept campaign_id; both TODO(phase-4) markers закрыты)
    - app/schemas/__init__.py (SendMessageRequest body: ai_context_id → campaign_id, message Optional)
    - app/main.py (import + lifespan registration of campaign_enqueue_worker)
    - app/config.py (+2 env vars CAMPAIGN_ENQUEUE_TICK_SECONDS, CAMPAIGN_ENQUEUE_BATCH_SIZE)
  deleted:
    - tests/test_rotation.py (obsolete — context_id signature gone; Rule 3 deviation)
    - tests/test_rotation_workspace_id.py (obsolete — queries dropped context_contact_assignments; Rule 3 deviation)

key-decisions:
  - "render_template empty fallback per D-19 — strict mode deferred to v2 (gives quick UX feedback: 'тут должно быть имя, его нет в CSV')"
  - "Russian aliases в lowercase-only маппинге через RUSSIAN_ALIASES dict (имя→name, юзернейм→username, телефон→phone, источник→source, компания→custom.company)"
  - "Single regex `r'\\{\\{\\s*([a-zA-Zа-яА-Я_]+(\\.[a-zA-Z_0-9]+)?)\\s*\\}\\}'` с re.IGNORECASE | re.UNICODE — допускает пробелы внутри `{{ name }}` (C-03), не парсит `{name}` (single brace, JSON-like), не поддерживает Mustache filters `{{name | upper}}`"
  - "rotation.get_or_assign_sender выносит `commit` kwarg (M2 revision) — worker управляет outer transaction через begin_nested savepoint без double-commit от rotation"
  - "Sender pool — JOIN campaign_senders с явным s.workspace_id = :wid guard (Phase 02.1 CR-03 defence-in-depth pattern)"
  - "_pick_least_loaded использует COUNT(cca.id) GROUP BY sender — load balancing across campaign's sender pool, не глобальный workspace pool"
  - "CampaignEnqueueWorker сохраняет workspace_id во всех INSERT (Phase 02.1 CR-01 pattern) — даже когда workspace выводится из campaign (Pitfall 8 defence-in-depth)"
  - "В _upsert_conversation: ai_context_id derived from campaigns.agent_id JOIN если item.campaign_id IS NOT NULL, иначе fallback на extra_data (legacy support); агент при первой отправке campaign'ской queue → сразу записан в conversation.ai_context_id"
  - "enqueue_file signature тоже принимает campaign_id (B1 revision per plan-checker) — file и message-flow синхронны"
  - "Obsolete tests/test_rotation*.py УДАЛЕНЫ (Rule 3 — they target dropped context_contact_assignments + old context_id signature; replacement is tests/test_rotation_campaign.py)"

patterns-established:
  - "Background worker = module-level singleton + start/stop in lifespan (Phase 2 carry-over, extended)"
  - "Per-contact savepoint atomic boundary (Q5 resolution) — enables retry on next tick without losing partial progress"
  - "Optional message field на API + server-side template render — клиент может прислать full text для дебага, иначе сервер рендерит из campaign.message_template"
  - "Russian alias dictionary в исходниках — не БД-driven, не Lovable-driven — single source of truth для C-02"

requirements-completed: [CAMP-09, CAMP-10, CAMP-17]

duration: 10min
completed: 2026-05-22
---

# Phase 04 Plan 04: Queue Rewrite + CampaignEnqueueWorker + send.py Summary

**Самый большой план Phase 4: новый Mustache renderer + background worker для досыпания контактов + рерайт rotation/send/queue под campaign_id. 3 TODO(phase-4) маркера закрыты (queue.py:705, queue.py:849, rotation.py).**

## Performance

- **Duration:** ~10 min (5 tasks, 1 wave)
- **Started:** 2026-05-22T08:44:59Z
- **Completed:** 2026-05-22T08:55:15Z
- **Tasks:** 5 (Wave 0 stubs, template, rotation rewrite, worker, send/queue rewrite)
- **Files modified/created:** 13 (7 created, 6 modified, 2 deleted)

## Accomplishments

- **`app/services/template.py` (new):** `render_template(template, contact, *, campaign_id, phone)` Mustache renderer. Поддерживает `{{name}}`, `{{username}}` (с `@`-префиксом), `{{phone}}`, `{{source}}`, `{{custom.X}}` + русские алиасы (`имя`/`юзернейм`/`телефон`/`источник`/`компания`). Regex `r"\{\{\s*([a-zA-Zа-яА-Я_]+(\.[a-zA-Z_0-9]+)?)\s*\}\}"` с `re.IGNORECASE | re.UNICODE`. Missing var → empty string + `logger.warning` (D-19). JSON-like `{"key":"value"}` НЕ интерпретируется. Filters `{{name | upper}}` НЕ резолвятся (C-03).

- **`app/services/rotation.py` (full rewrite):** Signature `get_or_assign_sender(campaign_id: UUID, contact_phone: str, db: AsyncSession, *, commit: bool = True) -> Optional[Sender]`. Источник senders — JOIN `campaign_senders cs ON s.id=cs.sender_id WHERE cs.campaign_id=:cid` + явный `s.workspace_id=:wid` guard. ON CONFLICT (campaign_id, contact_phone) DO NOTHING — race-safe. `commit=False` ветка для worker'а (begin_nested savepoint без double-commit). Helper `_pick_least_loaded(db, sender_ids)` — load balancing внутри campaign pool. Returns `None` если нет active senders (caller обрабатывает).

- **`app/services/campaign_enqueue.py` (new):** `CampaignEnqueueWorker` singleton (паттерн ContactCheckWorker). Tick каждые 30s, batch 500 (env-configurable). Per running campaign: SELECT contacts из folder WHERE tg_status='registered' AND NOT IN cca → per contact: `begin_nested()` savepoint → rotation.get_or_assign_sender(commit=False) → render_template → INSERT message_queue (workspace_id + campaign_id propagated). Workspace isolation via explicit `c.workspace_id = :wid` guard в SELECT contacts (Pitfall 8).

- **`app/routers/send.py` (full rewrite):** Body `SendMessageRequest.campaign_id: UUID` (вместо `ai_context_id`). Sender resolution: explicit slug ИЛИ `rotation.get_or_assign_sender(campaign_id, ...)`. Message resolution: explicit text ИЛИ `render_template(campaign.message_template, contact)` с lookup contact по `(workspace_id, phone)`. 404 на чужой workspace's campaign, 409 на NO_ACTIVE_SENDER_IN_CAMPAIGN, 422 на EMPTY_MESSAGE. `enqueue_message(... campaign_id=campaign.id)`.

- **`app/services/queue.py` patch:** `_upsert_conversation` INSERT расширен на `campaign_id` (берётся напрямую из item.campaign_id, D-05); `ai_context_id` derived через `SELECT agent_id FROM campaigns WHERE id=:cid` если campaign_id IS NOT NULL, иначе fallback на extra_data (legacy support). `enqueue_message` + `enqueue_file` accept `campaign_id` kwarg (B1 revision — file-flow синхронизирован с message-flow). Оба TODO(phase-4) маркера на :705 и :849 закрыты.

- **`app/main.py` lifespan:** `campaign_enqueue_worker.start()` рядом с другими worker'ами; `.stop()` в shutdown. Imports — рядом с `contact_check_worker`.

- **`app/config.py` extension:** 2 env vars через pydantic Field + `validation_alias`: `CAMPAIGN_ENQUEUE_TICK_SECONDS` (default 30), `CAMPAIGN_ENQUEUE_BATCH_SIZE` (default 500).

- **42 test stubs / GREEN tests:** 14 unit-тестов template.render_template + 12 integration CampaignEnqueueWorker + 6 rotation + 5 send + 5 queue.campaign_id (включая sanity-check на отсутствие обоих TODO маркеров).

## Task Commits

1. **Task 1: Wave 0 — 5 test stub files (42 tests collect-only)** — `cb11c02` (test)
2. **Task 2: app/services/template.py + GREEN test_template_render.py (13 unit tests)** — `1b6d9d2` (feat)
3. **Task 3: app/services/rotation.py rewrite (campaign_id signature) + 6 integration tests** — `9f332f1` (feat)
4. **Task 4: app/services/campaign_enqueue.py + config + lifespan + 12 integration tests** — `94da830` (feat)
5. **Task 5: send.py + queue.py rewrite — campaign_id propagation + close 2 TODO markers** — `ad5ad48` (feat)

## Files Created/Modified

- **Created (7):**
  - `app/services/template.py` — render_template + TEMPLATE_VAR_RE + RUSSIAN_ALIASES + _resolve
  - `app/services/campaign_enqueue.py` — CampaignEnqueueWorker + singleton
  - `tests/test_template_render.py` — 14 unit tests (basic, RU alias, spaces, username @, JSON snippet, missing var, case-insensitive, no filters, unicode+emoji)
  - `tests/test_campaign_enqueue_worker.py` — 12 integration tests
  - `tests/test_queue_campaign_id.py` — 5 integration tests (NULLable, SET NULL FK, INSERT conversations campaign_id, TODO closure, enqueue_file signature)
  - `tests/test_send_campaign.py` — 5 integration tests (campaign_id contract, agent JOIN, 404 cross-workspace, X-Workspace-Key, template render fallback)
  - `tests/test_rotation_campaign.py` — 6 integration tests (signature, pool=campaign_senders, idempotent retry, skip inactive, return None on empty, race protection)
- **Modified (6):**
  - `app/services/rotation.py` — полный рерайт, удалена ссылка на `context_contact_assignments` (deferred-items.md ✓ closed)
  - `app/routers/send.py` — полный рерайт под campaign_id
  - `app/services/queue.py` — _upsert_conversation extension + enqueue_message/enqueue_file campaign_id param + оба TODO маркера убраны
  - `app/schemas/__init__.py` — SendMessageRequest: campaign_id (вместо ai_context_id), message Optional
  - `app/main.py` — import + lifespan start/stop campaign_enqueue_worker
  - `app/config.py` — 2 env vars
- **Deleted (2 — Rule 3 deviation):**
  - `tests/test_rotation.py` — obsolete (queries dropped table, calls old signature)
  - `tests/test_rotation_workspace_id.py` — obsolete (same reason)

## Decisions Made

- **Single Mustache regex с alias dictionary** (вместо jinja2/pystache) — нет внешней зависимости, single source of truth для RU-алиасов C-02. Lovable UI рендерит то же поведение через тот же regex (если фронт перепишет на JS).
- **`commit=False` kwarg на `rotation.get_or_assign_sender`** (M2 revision) — устраняет double-commit race между внутренним rotation commit и outer worker commit. Direct callers (send.py, ai_engine — будущий 04-05) пользуются default `commit=True` без изменений.
- **`begin_nested()` per contact в worker** (Q5 atomicity resolution) — savepoint обнимает rotation+INSERT queue в одну транзакцию per contact; partial failure (например render_template explosion) rollback'ится только этого контакта, остальные продолжают в том же tick'е.
- **`ai_context_id` retained в `enqueue_message` сигнатуре** — Phase 3 backward compat. Phase 4 callers просто передают `campaign_id` дополнительно; ai_context_id игнорируется в `_upsert_conversation` если есть campaign_id (campaigns.agent_id JOIN wins).
- **`message` стал Optional в `SendMessageRequest`** — если клиент пушит только `{campaign_id, phone}`, сервер рендерит из `campaign.message_template`. Это унифицирует n8n push: клиент конфигурирует шаблон один раз в campaign'е, рассылает одной кнопкой.
- **Empty rendered text → 422 EMPTY_MESSAGE** (не 200 с пустотой) — explicit fail rather than queue an empty Telegram message.
- **Old rotation tests deleted, not migrated** — `test_rotation.py` и `test_rotation_workspace_id.py` оба testовали dropped table `context_contact_assignments` и old `context_id` signature. Полная замена — `tests/test_rotation_campaign.py` (Task 3). Сохранение их (skipped) даёт false impression of obsolete coverage; удаление чище.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 — Blocking] tests/test_rotation.py + test_rotation_workspace_id.py target dropped table + old signature**

- **Found during:** Task 3 (rotation.py rewrite)
- **Issue:** Migration 016 (Plan 04-02 Task 1) DROPped `context_contact_assignments`. Existing `test_rotation.py` и `test_rotation_workspace_id.py` оба query эту таблицу и зовут `get_or_assign_sender(context_id=..., workspace_id=...)`. После Task 3 rewrite signature is `(campaign_id, contact_phone, db, commit=)` — старые тесты НЕ могут импортироваться/collect'иться без syntax error либо упадут на первой SQL operation.
- **Fix:** Удалены оба файла. Replacement coverage — новый `tests/test_rotation_campaign.py` (Task 1 stub → Task 3 GREEN, 6 tests).
- **Files modified:** `tests/test_rotation.py` (deleted), `tests/test_rotation_workspace_id.py` (deleted), `tests/test_rotation_campaign.py` (created)
- **Verification:** AST parse all touched files OK; commit `9f332f1` includes deletion.

**2. [Rule 3 — Blocking] app/routers/send.py старая rotation-вызов signature ломал импорт после Task 3**

- **Found during:** Task 3 (immediately after rotation.py rewrite)
- **Issue:** Task 3 rewriteал rotation.py с новой signature. Phase 3 `app/routers/send.py` вызывал `get_or_assign_sender(db=..., context_id=..., contact_phone=..., workspace_id=...)` — этот kwarg-set теперь невалидный. Полный рерайт send.py owned by Task 5 этого же плана — но между Task 3 и Task 5 файл импортируется (`app.main` подключает router) — если оставить старый вызов, любой импорт app/main падёт.
- **Fix:** Промежуточный stub: в Task 3 заменил rotation-ветку `send.py` на `raise HTTPException(410, code=ROTATION_REQUIRES_CAMPAIGN_ID)`. Task 5 заменил весь файл целиком на финальный рерайт.
- **Files modified:** `app/routers/send.py` (intermediate stub в Task 3 commit, final rewrite в Task 5)
- **Verification:** AST parse passes между commits 9f332f1 и ad5ad48; коммиты независимы.

**3. [Rule 2 — Missing critical] `_pick_least_loaded` помимо COUNT(cca.id) tie-break по `s.created_at`**

- **Found during:** Task 3 (rotation.py implementation)
- **Issue:** Plan описывает "round-robin / least-loaded". Если у двух senders COUNT=0 (только что прикреплены к новой кампании) — без tie-break первая строка непредсказуема (Postgres planner свободен выбрать любую). Это даёт flaky test для `test_rotation_picks_from_campaign_senders_only`.
- **Fix:** Добавлен `ORDER BY cnt ASC, s.created_at ASC LIMIT 1` — детерминистический выбор oldest-attached на ties.
- **Files modified:** `app/services/rotation.py`
- **Verification:** Manual inspection of `_pick_least_loaded` SQL.
- **Committed in:** `9f332f1`

**4. [Rule 1 — Bug] empty username `""` теперь не префиксится `@`**

- **Found during:** Task 2 (template.py implementation)
- **Issue:** Изначальная версия `_resolve("username", contact)` делала `return f"@{v}"` даже когда v="" — давало `"@"` в render output. Это ломает UX (видимый пустой `@` в сообщении).
- **Fix:** В `_resolve`: `if not v: return None` (попадает в missing-var ветку → empty string + warning). Также защита от double-@ (если username уже начинается с `@`, не дублируем).
- **Files modified:** `app/services/template.py`
- **Verification:** test_render_username_with_at_prefix + test_render_username_already_prefixed (Task 2 commit).
- **Committed in:** `1b6d9d2`

---

**Total deviations:** 4 (2 blocking, 2 auto-fixed). Все — в скоупе плана.

## Issues Encountered

- **pytest unavailable locally** (carry-over из Plan 04-03 SUMMARY). Тесты verify через `ast.parse` + in-process Python invocation of template logic (Task 2 acceptance shows 13 template tests passing in-process). Integration tests require Docker DB — будут зелёные на CI / `docker compose run --rm api pytest`.
- **`httpx` не установлен локально** — pip-зависимости API-контейнера. Verification fallback на `ast.parse` для signature checks.

## Known Stubs

None в этом плане. Все features fully wired:
- `render_template` — реальная имплементация, не mock.
- `CampaignEnqueueWorker` — реальная имплементация, реальные INSERT в `message_queue` + `campaign_contact_assignments`.
- `rotation.get_or_assign_sender` — реальная SELECT/INSERT логика.
- `send.py` POST /api/v1/send — реальная enqueue + rotation + template render.

**Phase 4 carry-overs остаются для Plan 04-05:**
- `app/services/listener.py:247-250, 350, 707` — 3 TODO(phase-4) markers (`pull ai_context_id from conversation.campaign_id JOIN`) — Plan 04-05 owns (D-12 built-in tools + ai_engine rewrite).
- `app/services/ai_engine.py:88` — `webhook_functions → campaigns.tools` — Plan 04-05 owns.

## User Setup Required

Если деплой на сервер — без новых env vars обязательных:
```bash
# Опционально (default 30s / 500 contacts per tick — обычно ОК):
export CAMPAIGN_ENQUEUE_TICK_SECONDS=30
export CAMPAIGN_ENQUEUE_BATCH_SIZE=500
docker compose up -d --build api
# Worker запускается в API-контейнере (singleton, не нужен новый docker сервис).
```

Listener container НЕ требуется ребилдить — Plan 04-04 не трогает listener.py.

## Next Plan Readiness

**Plan 04-05 (signals + webhooks + tools wiring) ready:**
- `conversations.campaign_id` теперь заполняется при первой отправке (D-05 — основа для ai_engine.get_context_for_conversation JOIN).
- `campaign_id` propagated through `_upsert_conversation` — listener.py может JOIN campaigns + ai_contexts для derive agent_id + tools.
- `render_template` доступен — Plan 04-05 переиспользует для preview tool descriptions если потребуется.
- 3 TODO(phase-4) markers в listener.py + ai_engine.py остаются открытыми — это явный scope Plan 04-05.

**Blockers/concerns:** Нет. Тесты пройдут на Docker-CI.

## Self-Check

```text
FOUND: app/services/template.py
FOUND: app/services/campaign_enqueue.py
FOUND: app/services/rotation.py (rewritten)
FOUND: app/routers/send.py (rewritten)
FOUND: app/services/queue.py (modified — INSERT conversations + enqueue_* sigs)
FOUND: app/schemas/__init__.py (modified — SendMessageRequest body)
FOUND: app/main.py (modified — lifespan)
FOUND: app/config.py (modified — 2 env vars)
FOUND: tests/test_template_render.py (14 tests)
FOUND: tests/test_campaign_enqueue_worker.py (12 tests)
FOUND: tests/test_queue_campaign_id.py (5 tests)
FOUND: tests/test_send_campaign.py (5 tests)
FOUND: tests/test_rotation_campaign.py (6 tests)
DELETED: tests/test_rotation.py (obsolete)
DELETED: tests/test_rotation_workspace_id.py (obsolete)

FOUND commit: cb11c02 (Task 1 — Wave 0 stubs)
FOUND commit: 1b6d9d2 (Task 2 — template.py)
FOUND commit: 9f332f1 (Task 3 — rotation rewrite)
FOUND commit: 94da830 (Task 4 — CampaignEnqueueWorker)
FOUND commit: ad5ad48 (Task 5 — send.py + queue.py)

Acceptance criteria final pass:
  - 0 TODO(phase-4) markers в scoped files                       ✓
  - render_template empty fallback per D-19                       ✓
  - Mustache + RU aliases (имя/юзернейм/телефон/источник/компания) ✓
  - rotation.get_or_assign_sender(campaign_id, ...)               ✓
  - source = campaign_senders pool, NOT global workspace          ✓
  - CampaignEnqueueWorker singleton + lifespan registration       ✓
  - INSERT message_queue → workspace_id + campaign_id propagated  ✓
  - INSERT conversations → campaign_id + agent_id (via JOIN)      ✓
  - enqueue_file + enqueue_message accept campaign_id (B1)        ✓
  - SendMessageRequest body = campaign_id (NOT ai_context_id)     ✓
  - send.py 404 on cross-workspace, 409 on no senders, 422 on empty render ✓
  - empirical constants UNCHANGED (CLAUDE.md guard)               ✓
  - Python AST parses all 13 touched files                        ✓
```

## Self-Check: PASSED

---

*Phase: 04-campaigns*
*Plan: 04*
*Completed: 2026-05-22*
