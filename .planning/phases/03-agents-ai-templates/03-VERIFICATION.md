---
phase: 03-agents-ai-templates
verified: 2026-05-22T00:00:00Z
status: human_needed
score: 4/4 must-haves verified (automated); 2 items pending human run
human_verification:
  - test: "Run pytest suite on server (Docker Postgres environment)"
    expected: "All 27 Phase 3 tests pass: 12 plan-03-01 (test_migration_015, test_ai_engine, test_listener, test_rotation, test_queue_enqueue, test_senders Phase 3 test) + 15 plan-03-02 (12 test_agents + 3 test_send). Phase 1+2 regression suite remains green."
    why_human: "Локальная среда (macOS Python 3.14 + SQLAlchemy 2.0.25 + нет Docker/Postgres) не может прогнать pytest — known project-wide constraint, documented in both SUMMARY.md files. Verification ограничена grep + AST + ручной проверкой кода. Реальный прогон должен быть на DigitalOcean сервере: `cd /root/apps/outreach-platform && git pull && docker compose up -d --build api && docker compose exec api pytest tests/test_agents.py tests/test_send.py tests/test_migration_015.py tests/test_ai_engine.py tests/test_listener.py tests/test_rotation.py tests/test_queue_enqueue.py tests/test_senders.py -x -v`"
  - test: "Live smoke test: full Phase 3 user flow"
    expected: "1) POST /api/v1/agents с JWT → 201 + AgentResponse {id, name, campaign_count: 0}. 2) POST /api/v1/agents с тем же name → 409 AGENT_NAME_DUPLICATE. 3) POST /api/v1/agents/{id}/duplicate → 201 + новый агент 'Name (copy)'. 4) POST /api/v1/send без ai_context_id → 422. 5) POST /api/v1/send с ai_context_id чужого workspace → 404 AGENT_NOT_FOUND. 6) POST /api/v1/send с валидным agent + sender → 200 + queue_id."
    why_human: "Требует live FastAPI + Postgres + Supabase JWT. Эти end-to-end checks доказывают Goal Achievement на runtime уровне (не только грэп). Cross-checks docker compose start, миграция 015 успешно применилась через init_db flow, оба роутера mounted и отвечают на корректные URL."
---

# Phase 3: Agents (AI Templates) Verification Report

**Phase Goal:** Клиент создаёт переиспользуемые AI-агентов на уровне workspace — каждый агент содержит контекст / задачу / тон / FAQ и используется в нескольких кампаниях.
**Verified:** 2026-05-22
**Status:** human_needed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths (Success Criteria from ROADMAP)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Пользователь создаёт агента с именем, задаёт контекст / задачу / тон / FAQ | ✓ VERIFIED | POST `/api/v1/agents` принимает AgentCreate {name, system_prompt, rules, tone_of_voice, faq, company_info, product_info} → 201; agents.py:137-176 + schemas/__init__.py:407-415 |
| 2 | Существующая модель `ai_contexts` переиспользуется (без переименования), но отвязывается от sender'а — становится workspace-level | ✓ VERIFIED | migrations/015_phase3.sql drops senders.ai_context_id (line 20); ORM Sender no longer has ai_context_id attribute (models/__init__.py:73-101 — explicit comment line 96); ai_contexts table retained with workspace_id FK |
| 3 | Тот же агент можно подключать в нескольких кампаниях | ✓ VERIFIED | AGNT-03 закрыт reusability теста (test_send.py:57 test_same_agent_id_works_for_multiple_senders) — один agent_id успешно передаётся в POST /send с разными sender_slug. Sender больше не привязан к agent через FK — связь только через explicit body параметр |
| 4 | Страница списка агентов показывает: имя, кол-во кампаний где использован, кнопки дубликата и удаления | ✓ VERIFIED (with documented stub) | GET /api/v1/agents возвращает AgentListResponse {agents: [...], total: N} с полем campaign_count в каждом AgentResponse; POST /duplicate + DELETE endpoints реализованы. **Note:** campaign_count=0 hardcoded — accepted Phase 4 boundary stub (D-10), документирован в SUMMARY 03-02 + TODO(phase-4) маркер |

**Score:** 4/4 truths verified automatically (level 3); 2 items need human-runnable verification (pytest + live smoke)

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `migrations/015_phase3.sql` | Idempotent migration: drops 6 ai_contexts columns + senders.ai_context_id, adds UNIQUE INDEX | ✓ VERIFIED | 27 lines; BEGIN/COMMIT; `DROP COLUMN IF EXISTS` x7; `CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_contexts_workspace_name` |
| `app/models/__init__.py` AIContext class | Clean ORM: только id/workspace_id/name/system_prompt/tone_of_voice/rules/company_info/product_info/faq/created_at/updated_at; нет senders relationship | ✓ VERIFIED | models/__init__.py:146-164; deprecated fields отсутствуют; explicit NB-комментарий о D-01/D-04 cleanup |
| `app/models/__init__.py` Sender class | Без ai_context_id Column + ai_context relationship | ✓ VERIFIED | models/__init__.py:73-101; line 96 explicit comment about drop; only messages/contacts relationships остались |
| `app/routers/agents.py` | 6 workspace-scoped endpoints под /api/v1/agents | ✓ VERIFIED | 297 lines; APIRouter(prefix="/api/v1/agents"); 6 endpoints: list_agents, create_agent, get_agent, update_agent, delete_agent, duplicate_agent; все используют `Depends(auth_dep)` + `.where(AIContext.workspace_id == ctx.workspace_id)` |
| `app/routers/send.py` | Rewritten under AuthDep with explicit ai_context_id в body | ✓ VERIFIED | 133 lines; нет verify_api_key/AIContext.is_active; workspace-scoped agent check (line 41-46); explicit ai_context_id propagated to enqueue_message (line 111) |
| `app/routers/contexts.py` | Deleted | ✓ VERIFIED | `ls` returns "No such file"; git log shows `824fe04 feat(03-02): rewrite app/routers/send.py + delete legacy contexts.py` |
| `app/main.py` | Both agents.router + send.router registered | ✓ VERIFIED | main.py:14 imports agents; main.py:20 imports send; main.py:91 + 92 include_router; contexts.router НЕ зарегистрирован |
| `app/services/queue.enqueue_message` | Accepts explicit ai_context_id | ✓ VERIFIED | queue.py:775-787; новый param `ai_context_id: Optional[UUID] = None`; queue.py:801-802 stores в `extra_data['ai_context_id']`; _upsert_conversation reads it back (line 709) |
| `app/services/ai_engine.py` | get_context без is_active/max_message_length/webhook_functions SELECT | ✓ VERIFIED | ai_engine.py:69-100; SQL только `SELECT system_prompt, tone_of_voice, rules, company_info FROM ai_contexts WHERE id = :id`; defaults (max_message_length=500, webhook_functions=[]) returned for downstream callers |
| `app/services/listener.py` | get_active_senders без ai_context_id; document_webhook_url block — no-op | ✓ VERIFIED | listener.py:341-372; SELECT только id/slug/phone/session_string/proxy; document_webhook_url block заменён на no-op log + TODO(phase-4) |
| `app/services/rotation.py` | _pick_best_sender workspace-only фильтр | ✓ VERIFIED | rotation.py:163-204; WHERE s.workspace_id = :wid AND s.lifecycle_status='active' AND s.auth_status='ok' AND s.role='sender'; context_id параметр оставлен в сигнатуре (backward compat per D-05) |
| `app/schemas/__init__.py` | AgentCreate/Update/Response/ListResponse + FaqItem; SendMessageRequest требует ai_context_id | ✓ VERIFIED | schemas/__init__.py:399-449 (Agent block); SendMessageRequest line 17-26 с `ai_context_id: UUID = Field(...)` (required) |
| `tests/test_agents.py` | Reasonable coverage of AGNT-01..04 | ✓ VERIFIED | 12 async-tests, 278 lines: create (3) + fields (2) + list/patch (2) + delete (2) + duplicate (2) + race (1). Note: SUMMARY 03-02 explicitly documents 12 tests (not 13 from VALIDATION naming map — naming gap explained, coverage equivalent) |
| `tests/test_send.py` | 3 tests covering required ai_context_id + cross-workspace + reusability | ✓ VERIFIED | 3 async-tests: test_send_requires_ai_context_id, test_send_cross_workspace_agent_404, test_same_agent_id_works_for_multiple_senders |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|----|--------|---------|
| `app/main.py` | agents.router + send.router | `app.include_router(...)` | ✓ WIRED | main.py:91-92 register both routers |
| `POST /api/v1/agents/{id}/duplicate` | AIContext name auto-increment | `_generate_duplicate_name` LIKE-based | ✓ WIRED | agents.py:88-112 (helper) + agents.py:255-296 (endpoint with retry-on-IntegrityError loop, max 5 attempts, Pitfall 2 protection) |
| `POST /api/v1/send` | AIContext.workspace_id check | `.where(AIContext.id == request.ai_context_id, AIContext.workspace_id == ctx.workspace_id)` | ✓ WIRED | send.py:41-46 |
| `DELETE /api/v1/agents/{id}` | context_contact_assignments каскад | FK ON DELETE CASCADE | ✓ WIRED | migration 007 line 10 (`REFERENCES ai_contexts(id) ON DELETE CASCADE`); conversations.ai_context_id ON DELETE SET NULL (models/__init__.py:229) |
| `tests/conftest.py` | migrations/015_phase3.sql | exec_driver_sql после 013/014 | ✓ WIRED | conftest.py:61-62 reads and applies sql_015 |
| `app/services/queue.enqueue_message` | conversations.ai_context_id propagation | extra_data['ai_context_id'] channel | ✓ WIRED | queue.py:801-802 stores; queue.py:709 reads back for INSERT INTO conversations |
| `app/routers/send.py` | enqueue_message ai_context_id parameter | explicit keyword arg | ✓ WIRED | send.py:111 (`ai_context_id=request.ai_context_id`) |
| `app/models/__init__.py` Sender | No ai_context_id Column + relationship | удаление Column + relationship | ✓ WIRED | models/__init__.py:73-101 — column and relationship both absent (verified via Python smoke check noted in SUMMARY 03-01) |

### Data-Flow Trace (Level 4)

Phase 3 produces backend API endpoints (no UI components) — Level 4 applies to the API → DB → API contract path:

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|-------------------|--------|
| `GET /api/v1/agents` agents list | `agents` (List[AgentResponse]) | `select(AIContext).where(AIContext.workspace_id == ctx.workspace_id)` | Yes — actual ORM query against ai_contexts (after migration 015 clean schema) | ✓ FLOWING |
| `POST /api/v1/agents` AgentResponse | new agent row | `db.add(agent) + db.commit() + db.refresh(agent)` — real INSERT | Yes — id/created_at populated from DB | ✓ FLOWING |
| `GET /api/v1/agents/{id}` AgentResponse | single agent | `_load_agent` SELECT by id + workspace_id | Yes — real lookup | ✓ FLOWING |
| `AgentResponse.campaign_count` | hardcoded `0` (D-10) | constant in `_agent_to_response` | **No — intentional Phase 4 boundary stub** | ⚠️ STATIC (ACCEPTED per user note + SUMMARY 03-02) |
| `POST /api/v1/send` returned EnqueueResponse | queue insertion result | `enqueue_message()` returns real `{queue_id, queue_position, estimated_send_at}` | Yes — real INSERT INTO message_queue | ✓ FLOWING |
| `POST /api/v1/agents/{id}/duplicate` AgentResponse | duplicated AIContext | INSERT + retry loop on IntegrityError | Yes — real DB write | ✓ FLOWING |

### Behavioral Spot-Checks

**Step 7b: SKIPPED (no runnable local environment)**

Local macOS env has Python 3.14 + SQLAlchemy 2.0.25 incompatibility + no Docker/Postgres. Both SUMMARY 03-01 and 03-02 explicitly document this project-wide constraint. Per user instructions in `<what_to_verify>`: "Verify by code-shape (read tests + grep), not by running pytest. This is a known project-wide constraint, not a phase 3 failure."

Behavioral verification deferred to human run on DigitalOcean server (see human_verification section).

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| AGNT-01 | 03-02 | Пользователь создаёт агента (AI-шаблон) с именем — workspace-level | ✓ SATISFIED | POST `/api/v1/agents` с {name} → 201; workspace_id берётся из AuthCtx; UNIQUE INDEX (workspace_id, name) в migration 015 + 409 на дубль (Pattern 2). Тест: test_create_agent_returns_201, test_create_agent_workspace_scoped, test_create_agent_duplicate_name_409 |
| AGNT-02 | 03-01, 03-02 | Задаёт настройки агента: контекст (промпт), задача, тон, FAQ | ✓ SATISFIED | AgentCreate содержит system_prompt, rules, tone_of_voice, faq (List[FaqItem]), company_info, product_info; persist через ORM AIContext; FAQ shape validation в FaqItem (question/answer). Тест: test_create_agent_persists_all_fields, test_faq_shape_validation |
| AGNT-03 | 03-01, 03-02 | Агент переиспользуется между несколькими кампаниями | ✓ SATISFIED | Sender отвязан от agent (D-04, senders.ai_context_id dropped); один ai_context_id передаётся в несколько POST /send запросов с разными sender_slug; rotation._pick_best_sender больше не фильтрует по ai_context_id. Тест: test_same_agent_id_works_for_multiple_senders. **Campaign-level wiring** придёт в Phase 4. |
| AGNT-04 | 03-02 | Список агентов workspace с CRUD (создать / редактировать / удалить, дубликат) | ✓ SATISFIED | 6 endpoints: GET list, POST create, GET by id, PATCH partial, DELETE hard, POST /duplicate. Все workspace-scoped через AuthDep. Тесты: test_list_agents_with_campaign_count, test_patch_agent_partial, test_patch_faq_replaces_not_merges, test_delete_agent_sets_conversation_to_null, test_delete_agent_cascades_assignments, test_duplicate_agent_auto_name, test_duplicate_race_handling |

**Orphaned requirements check:** REQUIREMENTS.md Traceability table maps AGNT-01..04 to Phase 3 (all 4); все declared в `requirements:` frontmatter одного из двух планов (AGNT-02/AGNT-03 в обоих, AGNT-01/AGNT-04 только в 03-02). Все требования accounted for ✓.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `app/routers/agents.py` | 49, 246 | `TODO(phase-4)` markers | ℹ️ Info | Documented Phase 4 boundary stubs (campaign_count = 0, no active-campaign block on DELETE). Both explicitly accepted per user instruction. No goal impact. |
| `app/services/listener.py` | 250, 350, 707 | `TODO(phase-4)` markers | ℹ️ Info | Documented carry-overs for Phase 4 Campaign wiring. No Phase 3 goal impact — these are intentional deferrals. |
| `app/services/queue.py` | 708, 849 | `TODO(phase-4)` markers | ℹ️ Info | enqueue_file legacy + queue conversation.campaign_id JOIN — Phase 4 scope. |
| `app/services/rotation.py` | 180 | `TODO(phase-4)` marker | ℹ️ Info | Campaign.sender_lock — Phase 4 scope. |
| `app/services/ai_engine.py` | 88-90 | `TODO(phase-4)` marker | ℹ️ Info | max_message_length / webhook_functions move to Campaign — Phase 4 scope. |
| `app/routers/agents.py` | 64 | `campaign_count=0,  # D-10` | ⚠️ Warning (ACCEPTED) | Intentional Phase 3→Phase 4 boundary stub (D-10). Documented in SUMMARY 03-02 Known Stubs. Per user instructions: "Treat these as ACCEPTED (documented deferrals to Phase 4, not gaps)." |
| `app/routers/agents.py` | 246-247 | `# TODO(phase-4): also block on active campaign attachment` | ⚠️ Warning (ACCEPTED) | Intentional Phase 3→Phase 4 boundary stub (D-09). Documented in SUMMARY 03-02 Known Stubs. Accepted. |

**No blocker anti-patterns. No undocumented stubs. All TODOs trace to Phase 4 work per ROADMAP.**

### Human Verification Required

#### 1. Run pytest suite on server (Docker Postgres environment)

**Test:**
```bash
cd /root/apps/outreach-platform
git pull
docker compose up -d --build api
docker compose exec api pytest \
  tests/test_migration_015.py tests/test_ai_engine.py tests/test_listener.py \
  tests/test_rotation.py tests/test_queue_enqueue.py tests/test_senders.py \
  tests/test_agents.py tests/test_send.py -x -v
```

**Expected:**
- ~27 tests pass total (12 plan-03-01 + 15 plan-03-02)
- 0 failures, 0 errors
- Phase 1+2 regression suite (`pytest tests/` excluding Phase 3 tests) remains green — миграция 015 не сломала ничего из старых тестов
- Конкретные acceptance criteria для каждого теста перечислены в обоих PLAN.md `<acceptance_criteria>` блоках

**Why human:** Локальная среда (macOS Python 3.14 + SQLAlchemy 2.0.25 incompatible + нет Docker/Postgres) не может прогнать pytest. Это **known project-wide constraint** (documented in both SUMMARY 03-01 Issues + SUMMARY 03-02 Issues + 03-01 Self-Check) — не Phase 3 failure. Verification ограничена статическим анализом (grep + AST + ручное чтение кода). Реальное выполнение тестов должно быть на DigitalOcean сервере или CI.

#### 2. Live smoke test: full Phase 3 user flow

**Test:**
```bash
# 1. Create agent
curl -X POST http://localhost:8000/api/v1/agents \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{"name":"Sales Agent","system_prompt":"be helpful","tone_of_voice":"friendly","faq":[{"question":"Q","answer":"A"}]}'
# Expected: 201 + AgentResponse {id, name:"Sales Agent", campaign_count:0, ...}

# 2. Duplicate name
curl -X POST http://localhost:8000/api/v1/agents \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{"name":"Sales Agent"}'
# Expected: 409 AGENT_NAME_DUPLICATE

# 3. Duplicate endpoint
curl -X POST http://localhost:8000/api/v1/agents/$AGENT_ID/duplicate \
  -H "Authorization: Bearer $JWT"
# Expected: 201 + AgentResponse {name:"Sales Agent (copy)", ...}

# 4. Send without ai_context_id
curl -X POST http://localhost:8000/api/v1/send \
  -H "Authorization: Bearer $JWT" \
  -d '{"recipient_phone":"+79991234567","message":"hi"}'
# Expected: 422 Unprocessable Entity

# 5. Send with cross-workspace agent
curl -X POST http://localhost:8000/api/v1/send \
  -H "Authorization: Bearer $JWT_OTHER_WORKSPACE" \
  -d '{"ai_context_id":"$AGENT_ID","recipient_phone":"+79991234567","message":"hi"}'
# Expected: 404 AGENT_NOT_FOUND

# 6. Successful send
curl -X POST http://localhost:8000/api/v1/send \
  -H "Authorization: Bearer $JWT" \
  -d '{"ai_context_id":"$AGENT_ID","recipient_phone":"+79991234567","message":"hi"}'
# Expected: 200 + EnqueueResponse {queue_id, queue_position, sender_slug, ...}
```

**Expected:** Все 6 шагов отвечают как specified. Также: миграция 015 успешно применилась через startup flow (`init_db` или ручной `exec_driver_sql`); FastAPI version=2.0.0-phase3; `/docs` OpenAPI отображает /api/v1/agents endpoints.

**Why human:** Требует live FastAPI + Postgres + Supabase JWT setup, а также Lovable UI smoke (`GET /api/v1/agents` рендерится без ошибок несмотря на hardcoded campaign_count=0 — D-10 design). Эти end-to-end проверки доказывают Goal Achievement на runtime уровне, а не только через статический анализ кода.

### Deferred Items

Per `.planning/phases/03-agents-ai-templates/deferred-items.md`:

- **`app/routers/contexts.py`** — RESOLVED in plan 03-02 Task 4 (deleted via `git rm`).
- **`app/routers/send.py`** — RESOLVED in plan 03-02 Task 4 (full rewrite under AuthDep, registered в main.py).

**Phase 4 carry-overs (per Plan 03-02 SUMMARY "Next Phase Readiness" + 7 TODO(phase-4) markers across app/):**

1. Real `campaign_count` query in `_agent_to_response` (replaces D-10 hardcoded 0) — needs Campaign model.
2. Block `DELETE /api/v1/agents/{id}` if active campaign references it (D-09) — needs `campaigns.agent_id` FK and `campaigns.status` filter.
3. Restore `/send-file` and `/send-batch` endpoints if needed (С-04) — Phase 4 scope decision.
4. Listener: read `campaign.agent_id` instead of `extra_data['ai_context_id']` (listener.py:255, 350).
5. `document_webhook_url` from `conversation.campaign_id` (listener.py:707) — Phase 4 CAMP-14.
6. `max_message_length` / `webhook_functions` from Campaign (ai_engine.py:88) — Phase 4 CAMP-10/15.
7. `_pick_best_sender` filter by `campaign.sender_lock` (rotation.py:180) — Phase 4 CAMP-04.
8. `enqueue_file` ai_context_id propagation (queue.py:849) — same pattern as `enqueue_message`.

### Gaps Summary

**No gaps.** All 4 Phase 3 truths are satisfied at code level. Both intentional Phase 3→Phase 4 boundary stubs (campaign_count=0, no DELETE-block on active campaigns) are:
- Documented in SUMMARY 03-02 Known Stubs section
- Marked with explicit TODO(phase-4) comments in code
- Mapped to Phase 4 requirements in REQUIREMENTS.md (CAMP-01..17)
- Explicitly accepted per user verification instructions

**Final blockers to "passed" status:** None at the code level. Pytest suite must be run on Docker environment to satisfy the "tests pass" success criterion from both PLANs (since AST/grep-only verification was used per known project constraint). Live smoke test should confirm runtime integration (router mounting + migration application + JWT auth).

Recommended next step: run the human_verification tests on the DigitalOcean server. If both pass → flip status to `passed`. If either fails → flip to `gaps_found` with specific gap details.

---

_Verified: 2026-05-22_
_Verifier: Claude (gsd-verifier)_
_Mode: Initial verification (no previous VERIFICATION.md found)_
_Method: Static analysis (grep + Read + AST shape checks) — environmental block on local pytest documented in both SUMMARY files_
