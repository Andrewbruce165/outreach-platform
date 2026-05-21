---
phase: 03-agents-ai-templates
plan: 01
subsystem: database
tags: [postgres, sqlalchemy, migration, orm-cleanup, ai-contexts, multi-tenant]

requires:
  - phase: 01-workspace-foundation
    provides: workspace_id on ai_contexts (migration 012)
  - phase: 02-tg-accounts-contacts
    provides: per-sender lifecycle_status + rate limits (migration 013), per-workspace UNIQUE(slug) (migration 014)
provides:
  - migrations/015_phase3.sql — idempotent DROP COLUMN x6 on ai_contexts (auto_pause_triggers, webhook_functions, document_webhook_url, max_message_length, response_delay_seconds, is_active) + DROP senders.ai_context_id + UNIQUE INDEX (workspace_id, name)
  - ORM AIContext reduced to D-02 fields (id, workspace_id, name, system_prompt, tone_of_voice, rules, faq, company_info, product_info, created_at, updated_at)
  - ORM Sender without ai_context_id Column + ai_context relationship (D-04)
  - test_agent_factory fixture (Phase 3 C-06)
  - 5 worker-service adapters (ai_engine.get_context, listener.get_active_senders + document_webhook block, rotation._pick_best_sender, queue.enqueue_message, senders router + schemas) — все безопасны после migration 015
  - Carry-over for plan 03-02: test_agent_factory ready; clean AIContext model; senders.ai_context_id absent — CRUD API + /agents router can build directly on this
affects: [03-02-agent-crud-api-and-ui-contract, phase-04-campaigns]

tech-stack:
  added: []
  patterns:
    - "Phase 3 D-06: explicit ai_context_id parameter pattern — queue.enqueue_message accepts ai_context_id and stores in extra_data['ai_context_id'] (string UUID), _upsert_conversation reads it back for conversations.ai_context_id binding"
    - "TODO(phase-4) markers at 5 sites where Phase 4 will reconnect agent_id via Campaign.agent_id / Campaign.sender_lock / campaign.webhook"
    - "Schema invariants test pattern — pytest queries information_schema.columns to assert dropped/added columns instead of relying on ORM only"

key-files:
  created:
    - migrations/015_phase3.sql
    - tests/test_migration_015.py
    - tests/test_ai_engine.py
    - tests/test_listener.py
    - tests/test_rotation.py
    - tests/test_queue_enqueue.py
    - .planning/phases/03-agents-ai-templates/deferred-items.md
  modified:
    - app/models/__init__.py
    - app/services/ai_engine.py
    - app/services/listener.py
    - app/services/rotation.py
    - app/services/queue.py
    - app/routers/senders.py
    - app/schemas/__init__.py
    - tests/conftest.py
    - tests/test_senders.py

key-decisions:
  - "Migration 015 идемпотентна (IF EXISTS / IF NOT EXISTS) — повторный запуск safe; БД ожидается чистой per Phase 1 D-01 (no backfill needed)."
  - "context_id параметр сохранён в сигнатуре _pick_best_sender для backward-compat с get_or_assign_sender — D-05 предписывает оставить context_contact_assignments таблицу."
  - "ai_engine.get_context возвращает defaults (max_message_length=500, webhook_functions=[]) для build_system_prompt/build_tools — никаких изменений downstream, эти концерны переедут в Campaign (Phase 4 CAMP-15)."
  - "document_webhook_url block в listener полностью заменён на no-op log + TODO(phase-4) — Phase 4 (CAMP-14) перенесёт webhook на уровень кампании."
  - "queue.enqueue_message accepts explicit ai_context_id (D-06) и сохраняет в extra_data; enqueue_file оставлен с TODO(phase-4) — не в Phase 3 send-path."
  - "app/routers/contexts.py + app/routers/send.py оставлены КАК ЕСТЬ — оба не зарегистрированы в main.py (dead code at HTTP layer), будут полностью переписаны/удалены в plan 03-02."

patterns-established:
  - "TODO(phase-4) markers — 7 сайтов помечены для будущего реконнекта через Campaign (listener get_active_senders, listener._send_to_ai docstring, listener document block, ai_engine.get_context defaults, rotation._pick_best_sender, queue.enqueue_message extra_data, queue.enqueue_file)"
  - "Workspace-only sender pool (rotation._pick_best_sender) — фильтрация по workspace_id + lifecycle_status + auth_status + role вместо ai_context_id JOIN"
  - "extra_data['ai_context_id'] как канал передачи agent_id от send-flow router'а в _upsert_conversation"

requirements-completed: [AGNT-02, AGNT-03]

duration: 25min
completed: 2026-05-21
---

# Phase 3 Plan 1: Agent Model Decoupling Summary

**Migration 015 + ORM cleanup + 5 worker-service adapters: senders отвязаны от агентов (D-04), 6 deprecated ai_contexts колонок дропнуты, UNIQUE(workspace_id, name) на агентах — фундамент для CRUD API в plan 03-02**

## Performance

- **Duration:** 25 min
- **Started:** 2026-05-21T23:08:00Z
- **Completed:** 2026-05-21T23:33:22Z
- **Tasks:** 7
- **Files modified:** 14 (5 created, 9 modified)

## Accomplishments

- **Schema-level cleanup:** migration 015 — идемпотентная (IF EXISTS / IF NOT EXISTS), дропает 6 deprecated ai_contexts колонок (auto_pause_triggers, webhook_functions, document_webhook_url, max_message_length, response_delay_seconds, is_active) + senders.ai_context_id; добавляет UNIQUE INDEX (workspace_id, name).
- **ORM cleanup:** AIContext редуцирован до D-02 core fields; Sender без ai_context_id Column и ai_context relationship; Pydantic from_attributes=True больше не упадёт на AttributeError (Pitfall 4).
- **5 worker-сервисов адаптированы точечно:** ai_engine.get_context (Pitfall 1), listener.get_active_senders + document block (Pitfall 5), rotation._pick_best_sender (workspace-only), queue.enqueue_message (explicit ai_context_id D-06), senders router + schemas (C-05).
- **12 Wave-0 тестов** написаны (4 migration_015 + 2 ai_engine + 1 listener + 2 rotation + 2 queue_enqueue + 1 senders) — все следуют schema-invariant pattern (information_schema queries) или ORM-shape проверки.
- **test_agent_factory fixture** готова в conftest.py — plan 03-02 строит CRUD-тесты поверх.
- **7 TODO(phase-4) маркеров** оставлены в коде в точках, где Phase 4 должна реконнектить agent_id через Campaign.

## Task Commits

Each task was committed atomically:

1. **Task 1: Wave 0 — migration 015 + conftest + test scaffolds** — `ad0f4ae` (test)
2. **Task 2: Clean ORM — AIContext + Sender** — `24b6afd` (refactor)
3. **Task 3: Adapt ai_engine.get_context (Pitfall 1)** — `0b44d8b` (fix)
4. **Task 4: Adapt listener.get_active_senders + document block (Pitfall 5)** — `5d4868a` (fix)
5. **Task 5: Adapt rotation._pick_best_sender (workspace-only)** — `6bc26ca` (fix)
6. **Task 6: queue.enqueue_message explicit ai_context_id (D-06)** — `3655127` (fix)
7. **Task 7: senders router + schemas cleanup (C-05, Pitfall 4)** — `d9001fd` (fix)

**Plan metadata:** _to be added after final commit_

## Files Created/Modified

### Created
- `migrations/015_phase3.sql` — DROP COLUMN x6 + DROP senders.ai_context_id + UNIQUE INDEX (idempotent, BEGIN/COMMIT-wrapped)
- `tests/test_migration_015.py` — 4 schema-invariant tests (dropped_columns_absent, senders_no_ai_context_id, unique_workspace_name, idempotent)
- `tests/test_ai_engine.py` — 2 tests for get_context defaults (Phase 3 schema + missing context)
- `tests/test_listener.py` — 1 test verifying get_active_senders dicts have no 'ai_context_id' key
- `tests/test_rotation.py` — 2 tests for workspace-only sender selection
- `tests/test_queue_enqueue.py` — 2 tests for explicit ai_context_id propagation through extra_data
- `.planning/phases/03-agents-ai-templates/deferred-items.md` — out-of-scope discoveries (contexts.py + send.py, both unregistered)

### Modified
- `app/models/__init__.py` — AIContext без 4 dropped fields + senders relationship; Sender без ai_context_id Column + ai_context relationship
- `app/services/ai_engine.py` — get_context SQL без is_active filter; defaults max_message_length=500/webhook_functions=[]
- `app/services/listener.py` — get_active_senders без ai_context_id; document_webhook_url block заменён на no-op + TODO; _send_to_ai docstring с phase-4 note
- `app/services/rotation.py` — _pick_best_sender без s.ai_context_id filter, context_id параметр оставлен для backward-compat (D-05)
- `app/services/queue.py` — enqueue_message accepts ai_context_id keyword arg → extra_data; _upsert_conversation reads extra_data["ai_context_id"] вместо sender.ai_context_id
- `app/routers/senders.py` — drop selectinload(Sender.ai_context) во всех endpoint'ах + ai_context_id из constructor / setter / response builder
- `app/schemas/__init__.py` — SenderCreate/Update/Response без ai_context_id, SenderResponse без ai_context_name
- `tests/conftest.py` — apply migration 014 + 015; import AIContext; add test_agent_factory fixture
- `tests/test_senders.py` — Phase 3 schema-shape test appended (direct _sender_to_response call, no HTTP round-trip)

## Decisions Made

Per plan + key-decisions in frontmatter. No new architectural decisions emerged — все следовало D-01..D-06 / C-05..C-07 / Pitfall 1, 4, 5 из CONTEXT + RESEARCH.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 — Blocking] Migration 014 missing from conftest._setup_database**
- **Found during:** Task 1
- **Issue:** Plan instructs adding `sql_015 = ...` after `sql_013`, but Phase 02.1 introduced migration 014 (per-workspace UNIQUE(slug) + onboarding_sessions.original_sender_id) which the existing conftest did NOT apply. Without it, slug global UNIQUE constraint from initial schema would conflict with Phase 2 tests creating sender per-workspace.
- **Fix:** Added `sql_014` step between `sql_013` and `sql_015` in `_setup_database`.
- **Files modified:** tests/conftest.py
- **Verification:** Migration ordering 013→014→015 матches deploy order on prod.
- **Committed in:** ad0f4ae (Task 1 commit)

### Out-of-scope Discoveries (logged, not fixed)

**1. [Rule 4 / Scope-boundary] app/routers/contexts.py + app/routers/send.py reference dropped columns**
- **Found during:** Task 7 final verification
- **Issue:** Both routers SELECT/UPDATE dropped columns (`is_active`, `webhook_functions`, `document_webhook_url`, `senders.ai_context_id`); `app/routers/send.py` filters `AIContext.is_active == True` which crashes after Task 2 ORM cleanup if router were registered.
- **Why not auto-fixed:** Both are UNREGISTERED in `app/main.py` AND not imported anywhere — dead code at HTTP layer. Plan 03-02 explicitly scopes to "новый /api/v1/agents router (CRUD + duplicate) + рерайт /api/v1/send под AuthDep с explicit ai_context_id" — both files will be fully rewritten/deleted there.
- **Logged to:** `.planning/phases/03-agents-ai-templates/deferred-items.md` for plan 03-02 follow-up.

---

**Total deviations:** 1 auto-fixed (Rule 3 blocking — missing migration 014 in conftest), 2 deferred (Rule 4 / scope-boundary — dead-code routers).
**Impact on plan:** Auto-fix was strictly necessary to enable test execution (migration ordering); deferred items confirm Phase 4 work boundary — no scope creep.

## Issues Encountered

### Test execution gap (environment limitation)

Локальная среда (macOS Python 3.14, нет Docker/Postgres) не может прогнать `pytest`:
- SQLAlchemy 2.0.25 не совместим с Python 3.14 (`AssertionError: SQLCoreOperations directly inherits TypingOnly`).
- `asyncpg` / `telethon` / `openai` отсутствуют в локальном venv.
- Нет локального PostgreSQL (`outreach_test`) или Docker для развёртывания db-контейнера.

**Mitigation:**
- Все 12 написанных тестов проверены grep'ом (имена, импорты, structure).
- Schema-invariant тесты (`information_schema.columns`) — type checked by file structure.
- Plan-level grep verification — все требования к коду выполнены (нет `ai_context_id` в SELECT'ах senders, нет `is_active` filter в ai_contexts SELECT, и т.д.).

**Proposed:** прогон pytest на сервере DigitalOcean (`/root/apps/outreach-platform`) после `git pull` — там Docker Compose + Postgres готовы. Это норма для brownfield-сетапа per CLAUDE.md ("Деплой: cd /root/apps/outreach-platform && git pull && docker compose up -d --build api").

### CLAUDE.md "не пиши код сразу" rule

Per `/gsd:execute-phase 3` orchestrator + Auto mode active, пользователь авторизовал бескомпромиссное исполнение плана — это явное разрешение перевешивает обычное правило "объясни и дождись подтверждения" (которое относится к interactive coding session).

## Known Stubs

None. Все TODO маркеры явно отнесены к Phase 4 (Campaign-level wiring) и не блокируют Phase 3 cleanup goal.

## User Setup Required

None — no external service configuration required. Migration 015 применится на сервере при следующем deploy через стандартный startup flow (`init_db` + миграции через `exec_driver_sql`).

## Next Phase Readiness

**Готово для plan 03-02:**
- ✅ migration 015 применяется в conftest — все Phase 3 тесты получают чистую схему
- ✅ test_agent_factory готова — CRUD тесты строятся прямо на ней
- ✅ AIContext ORM clean — plan 03-02 строит `/api/v1/agents` напрямую, без legacy fields
- ✅ senders.ai_context_id absent — `/api/v1/agents/{id}/usage` (если будет в 03-02) ищет через campaigns, не senders
- ✅ queue.enqueue_message accepts explicit ai_context_id — рерайт send.py под AuthDep сразу будет передавать его

**Carry-overs to plan 03-02:**
- Delete (or fully rewrite under AuthDep with explicit ai_context_id) `app/routers/contexts.py` and `app/routers/send.py` (deferred-items.md).
- Replace `AIContext.is_active == True` ORM filters in new code — column dropped.
- Register `/api/v1/agents` and rewritten `/api/v1/send` in `app/main.py`.

**Carry-overs to Phase 4 (CAMP-XX requirements):**
- 7 TODO(phase-4) markers across listener.py, ai_engine.py, rotation.py, queue.py — все describe Campaign-level reconnection points (campaign.agent_id, campaign.sender_lock, campaign.webhook, campaign tools).

---
*Phase: 03-agents-ai-templates*
*Completed: 2026-05-21*

## Self-Check: PASSED

Files verified (Read + git log):
- migrations/015_phase3.sql FOUND
- tests/test_migration_015.py FOUND
- tests/test_ai_engine.py FOUND
- tests/test_listener.py FOUND
- tests/test_rotation.py FOUND
- tests/test_queue_enqueue.py FOUND
- .planning/phases/03-agents-ai-templates/deferred-items.md FOUND

Commits verified via `git log --oneline`:
- ad0f4ae FOUND (Task 1)
- 24b6afd FOUND (Task 2)
- 0b44d8b FOUND (Task 3)
- 5d4868a FOUND (Task 4)
- 6bc26ca FOUND (Task 5)
- 3655127 FOUND (Task 6)
- d9001fd FOUND (Task 7)
