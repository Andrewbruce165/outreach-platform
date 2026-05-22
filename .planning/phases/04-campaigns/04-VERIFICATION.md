---
phase: 04-campaigns
verified: 2026-05-22T00:00:00Z
status: human_needed
score: 6/6 must-haves verified (automated); pytest + live smoke pending human run on server
human_verification:
  - test: "Apply migration 016 + run full Phase 4 pytest suite on Docker/Postgres environment"
    expected: "Migration 016 applies cleanly (idempotent — safe to re-run). All ~100+ Phase 4 tests pass across 11 files: test_migration_016, test_campaigns_model, test_campaign_router, test_sender_lock, test_campaign_schedule, test_queue_per_campaign_hours, test_template_render, test_campaign_enqueue_worker, test_queue_campaign_id, test_send_campaign, test_rotation_campaign, test_builtin_tools, test_campaign_webhooks, test_custom_tools_wiring. Phase 1/2/3 regression suite remains green."
    why_human: "Локальная среда (macOS, Python 3.14 incompatible с SQLAlchemy 2.0.25, нет Docker/Postgres) не может прогнать pytest — known project-wide constraint, унаследован из Phase 3 (см. 03-VERIFICATION.md human_verification). Verification ограничена статическим анализом (grep + Read + AST checks). Реальный прогон должен быть на DigitalOcean сервере: `cd /root/apps/outreach-platform && git pull && docker compose up -d --build api listener && docker compose exec api pytest tests/test_migration_016.py tests/test_campaigns_model.py tests/test_campaign_router.py tests/test_sender_lock.py tests/test_campaign_schedule.py tests/test_queue_per_campaign_hours.py tests/test_template_render.py tests/test_campaign_enqueue_worker.py tests/test_queue_campaign_id.py tests/test_send_campaign.py tests/test_rotation_campaign.py tests/test_builtin_tools.py tests/test_campaign_webhooks.py tests/test_custom_tools_wiring.py -x -v`"
  - test: "Live end-to-end smoke: создание + старт + рассылка + signal → webhook"
    expected: "1) POST /api/v1/campaigns с {name, agent_id, folder_id, sender_ids[], message_template='Привет, {{имя}}!', lead_webhook_url, work_hour_start=9, work_hour_end=20, timezone='Europe/Moscow'} → 201. 2) POST /api/v1/campaigns/{id}/start → 200 + status='running'. 3) Через 30s — CampaignEnqueueWorker tick видит контактов в folder с tg_status='registered', создаёт INSERTs в message_queue + campaign_contact_assignments (per-campaign UNIQUE). 4) Queue worker tick (отдельный — queue.py) пикапит pending → sends через Telethon с rendered template ('Привет, Иван!'). 5) AI отвечает контакту → built-in tool finish_conversation → UPDATE conversation.status='finished' + ai_enabled=false + POST на campaigns.finish_webhook_url с C-01 payload (event_type, campaign_id, conversation_id, workspace_id, contact{phone/name/username/source/custom}, reason, message_history_excerpt[20], timestamp). 6) Sender lock: POST /api/v1/campaigns с тем же sender_id и status='running' → 409 SENDER_LOCK_CONFLICT. 7) POST /api/v1/campaigns/{id}/pause → status='paused', queue worker SKIP'ает (INNER JOIN + WHERE status='running'). 8) Добавить контакт в folder via POST /api/v1/folders/{id}/contacts → следующий CampaignEnqueueWorker tick включает его в очередь (CAMP-09 top-up)."
    why_human: "Требует live FastAPI + Postgres + Telethon-session (реальные TG-аккаунты) + Lovable workspace + n8n webhook endpoint. Эти end-to-end проверки доказывают Goal Achievement на runtime уровне — статический анализ не может верифицировать актуальное поведение CampaignEnqueueWorker tick'а, fire-and-forget webhook delivery, или Telethon dispatch. Также UI smoke (Lovable рендерит is_exhausted + attached_senders[].locked_by_campaign_id корректно)."
---

# Phase 4: Campaigns Verification Report

**Phase Goal:** Клиент создаёт кампанию (объект-обёртка над рассылкой) — связывает агента + TG-аккаунты + папку контактов + сигналы (лид/менеджер/финиш) + webhook + tools + расписание, запускает её и видит как сообщения уходят.
**Verified:** 2026-05-22
**Status:** human_needed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths (Success Criteria from ROADMAP)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | User creates a campaign: picks agent, contact folder, TG accounts, sets schedule (hours/days/start-stop dates) | ✓ VERIFIED | POST `/api/v1/campaigns` принимает CampaignCreate {name, agent_id (FK ai_contexts), folder_id (FK folders), sender_ids[] (через campaign_senders), timezone, work_hour_start/end, work_days_mask, start_date, stop_date}. Все FK в migration 016 lines 18-29. CampaignCreate в schemas/__init__.py:499-528. Router endpoint campaigns.py:268. |
| 2 | Sets signals (lead/handoff/finish), webhook URL, optional tools spec | ✓ VERIFIED | campaigns table cols 31-37: lead/handoff/finish_webhook_url + lead/handoff/finish_trigger_hint + tools JSONB DEFAULT '[]'. CampaignCreate + CampaignUpdate принимают эти поля. ToolSpec Pydantic (schemas:473) валидирует custom-tools shape (мирорит recovered legacy webhook_functions). |
| 3 | Starts campaign — queue generated from folder contacts, sending via selected accounts with template variables `{{имя}}, {{username}}, {{source}}, {{custom.X}}` | ✓ VERIFIED | POST /api/v1/campaigns/{id}/start (campaigns.py:485) flips status='running' с sender-lock check; CampaignEnqueueWorker (services/campaign_enqueue.py:44) SELECTs contacts из folder WHERE tg_status='registered' AND NOT IN cca, INSERT'ит в message_queue с rendered template + campaign_id. template.py:81 render_template поддерживает все 4 переменные + RU aliases (имя/юзернейм/телефон/источник/компания). rotation.py:35 get_or_assign_sender picks from campaign_senders pool (НЕ workspace-wide). |
| 4 | Signal triggered → conversation status changes accordingly, webhook fires with event data | ✓ VERIFIED | ai_engine.py:41 `BUILT_IN_TOOL_NAMES = {mark_as_lead, transfer_to_manager, finish_conversation}`; build_builtin_tools всегда инжектит 3 OpenAI function tools (D-12). _handle_builtin_signal (line 218): UPDATE conversations SET status='lead'/'handoff'/'finished' + ai_enabled=false (для handoff/finish) + notify_signal call. webhook_notify.py:75 notify_signal — async fire-and-forget с C-01 payload (event_type/campaign_id/conversation_id/contact/reason/message_history_excerpt). Pitfall 1 priority dispatch: `_BUILTIN_PRIORITY = {finish:0, handoff:1, lead:2}` sorted DESC iterated → highest-priority wins. Q3 farewell: text_content + finish → text возвращается перед status flip. |
| 5 | Pauses/stops campaign; adding contact to folder during active campaign = topped up into queue | ✓ VERIFIED | POST /campaigns/{id}/pause (campaigns.py:526) + /resume (line 547) + /finish (line 575) — status flips. queue.py:184/302 INNER JOIN campaigns ON mq.campaign_id с WHERE status='running' AND (start_date IS NULL OR NOW() >= start_date) — paused/done campaigns не пикапятся ни в _tick ни в _process_next_for_sender (defence-in-depth race-safety). CAMP-09 top-up: CampaignEnqueueWorker tick'ает каждые 30s (default), SELECT'ит новые контакты из folder (NOT IN cca) и доливает в очередь. |
| 6 | Sender attached to active campaign cannot be selected for another active campaign (locked) | ✓ VERIFIED | campaigns.py:485 (/start) и :547 (/resume) проверяют sender lock via _check_sender_lock (helper) → 409 SENDER_LOCK_CONFLICT с {sender_id, campaign_id, campaign_name}. attached_senders[].locked_by_campaign_id вычисляется на read-time (GET /campaigns) — UI видит блокировку. campaign_senders through-table (PK campaign_id+sender_id) — sender может быть прикреплён к нескольким кампаниям, но lock срабатывает только когда какая-то из них running. |

**Score:** 6/6 truths verified automatically (level 3); pytest + live smoke pending human run on DigitalOcean server (carry-over project constraint inherited from Phase 3).

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `migrations/016_phase4.sql` | Idempotent migration: 3 new tables + 2 ALTER + 1 DROP + CHECK extension; Q1 (NULLable campaign_id) + Q6 (VARCHAR+CHECK status) overrides | ✓ VERIFIED | 113 lines; BEGIN/COMMIT; 5 `CREATE TABLE IF NOT EXISTS` (3 new) / 1 `DROP TABLE IF EXISTS context_contact_assignments` / `ALTER TABLE conversations ADD COLUMN IF NOT EXISTS campaign_id` / `ALTER TABLE message_queue ADD COLUMN IF NOT EXISTS campaign_id` / `DROP CONSTRAINT IF EXISTS conversations_status_check` + extend. Q1 verified on line 102-104 (`message_queue.campaign_id UUID REFERENCES campaigns(id) ON DELETE SET NULL` — NULLable). Q6 verified on line 23 (`status VARCHAR(20) NOT NULL DEFAULT 'draft'`) + line 40-41 (`CONSTRAINT campaigns_status_check CHECK (status IN ('draft','running','paused','done'))`). |
| `app/models/__init__.py` Campaign/CampaignSender/CampaignContactAssignment | 3 new ORM classes; ContextContactAssignment removed | ✓ VERIFIED | models/__init__.py:440 `class Campaign(Base)`; :488 `class CampaignSender(Base)`; :507 `class CampaignContactAssignment(Base)`. No `class ContextContactAssignment` (DROPped in 016). |
| `app/routers/campaigns.py` | 10 endpoints registered in main.py | ✓ VERIFIED | 10 `@router.{get,post,patch,delete}` decorators: POST list/get-id/patch/delete (CRUD) + /start /pause /resume /finish (lifecycle, 4) + /duplicate. main.py:98 `app.include_router(campaigns.router)`. |
| `app/services/queue.py` removes MOSCOW_TZ/WORK_HOUR_START/_END globals; adds `_campaign_in_working_window`; JOINs campaigns | All 3 globals + _is_working_hours/_next_working_window helpers removed; _campaign_in_working_window added; both _tick + _process_next_for_sender JOIN campaigns + check status='running'/start_date/window | ✓ VERIFIED | `grep -nE "MOSCOW_TZ\|WORK_HOUR_START\|WORK_HOUR_END\|_is_working_hours" app/services/queue.py` → 0 hits (only comment line 47 documenting removal). `_campaign_in_working_window` defined line 70; called line 207 + 323. `JOIN campaigns c ON c.id = mq.campaign_id` on lines 184 + 302. `QUEUE_TICK_BATCH = 500` line 66. |
| `app/services/template.py` | render_template + RUSSIAN_ALIASES dict + TEMPLATE_VAR_RE regex | ✓ VERIFIED | template.py:24 TEMPLATE_VAR_RE regex (`r"\{\{\s*([a-zA-Zа-яА-Я_]+(\.[a-zA-Z_0-9]+)?)\s*\}\}"`); :30 RUSSIAN_ALIASES dict; :49 alias resolution; :81 `def render_template(template, contact, *, campaign_id, phone)`. |
| `app/services/campaign_enqueue.py` | CampaignEnqueueWorker singleton with tick/start/stop + lifespan registration | ✓ VERIFIED | campaign_enqueue.py:44 `class CampaignEnqueueWorker`; :54 `def start`; :64 `async def stop`; :213 `campaign_enqueue_worker = CampaignEnqueueWorker()` singleton. main.py:13 import; :53 `.start()` в lifespan; :60 `.stop()` в shutdown. |
| `app/services/rotation.py` | Rewritten per-campaign (campaign_id + commit kwarg); pool = campaign_senders JOIN | ✓ VERIFIED | rotation.py:35 `async def get_or_assign_sender(campaign_id: UUID, contact_phone: str, db, *, commit: bool = True)`. Sender pool via `FROM campaign_senders cs JOIN senders s ON s.id = cs.sender_id WHERE cs.campaign_id = :cid` (line 113-117). NO references to `context_contact_assignments` (dropped table). |
| `app/services/webhook_notify.py` | notify_signal helper with fire-and-forget + C-01 payload | ✓ VERIFIED | webhook_notify.py:75 `async def notify_signal(event_type, campaign, conversation_id, contact, reason, db)`; :141 `asyncio.create_task(_fire(url, payload))` non-blocking; :30 `FROM messages WHERE conversation_id = :cid` (real history, NOT messages_log — auto-fix #1 in 04-05 SUMMARY). |
| `app/services/ai_engine.py` | BUILT_IN_TOOL_NAMES + build_builtin_tools + _handle_builtin_signal + get_context_for_conversation; priority dispatch | ✓ VERIFIED | ai_engine.py:41 BUILT_IN_TOOL_NAMES; :63 _BUILTIN_PRIORITY {finish:0,handoff:1,lead:2}; :66 build_builtin_tools; :124 get_context_for_conversation; :218 _handle_builtin_signal; :600 generate_response uses get_context_for_conversation; :638 build_builtin_tools merged with custom; :697 BUILT_IN_TOOL_NAMES dispatch; :707 sorted descending. |
| `app/routers/send.py` | Rewritten with campaign_id (was ai_context_id in Phase 3) | ✓ VERIFIED | send.py:23 `campaign_id: UUID = Field(...)` (REQUIRED — replaces Phase 3 ai_context_id). :87 workspace-scoped Campaign FK lookup. :140 rotation fallback. :181 `enqueue_message(... campaign_id=campaign.id)`. |
| `app/main.py` | campaigns router registered + campaign_enqueue_worker lifespan | ✓ VERIFIED | main.py:98 `include_router(campaigns.router)`. :13 import campaign_enqueue_worker. :53/60 start/stop. **Minor:** FastAPI(version="2.0.0-phase3") at line 72 still not bumped (root endpoint at line 106 does say "2.0.0-phase4") — cosmetic mismatch, no goal impact. |
| `app/config.py` | CAMPAIGN_ENQUEUE_TICK_SECONDS + CAMPAIGN_ENQUEUE_BATCH_SIZE env vars | ✓ VERIFIED | config.py:40 + :45 — both via pydantic Field + validation_alias. |
| `app/schemas/__init__.py` Pydantic | CampaignCreate/Update/Response/ListResponse + ToolSpec + ToolParamSpec + CampaignSenderAttach; SendMessageRequest with campaign_id | ✓ VERIFIED | schemas/__init__.py:17-34 SendMessageRequest (campaign_id REQUIRED, message Optional); :473 ToolSpec; :488 CampaignSenderAttach; :499 CampaignCreate (с @model_validator на work_hours); :530 CampaignUpdate; :556 CampaignResponse; :587 CampaignListResponse. |
| All 10 TODO(phase-4) markers closed | `grep -nrE "TODO\(phase-4\)" app/ --include="*.py"` returns 0 hits | ✓ VERIFIED | 0 hits across entire app/. B1 finalized per 04-05 SUMMARY checklist. |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|----|--------|---------|
| `app/main.py` | campaigns.router | `app.include_router(...)` | ✓ WIRED | main.py:98 |
| `app/main.py` | campaign_enqueue_worker | lifespan start/stop | ✓ WIRED | main.py:13 import + :53 start + :60 stop |
| `POST /api/v1/campaigns` | campaigns table | ORM Campaign + workspace_id from AuthCtx | ✓ WIRED | campaigns.py:268 endpoint → schemas/__init__.py:499 CampaignCreate → models/__init__.py:440 Campaign |
| `POST /api/v1/campaigns/{id}/start` | sender lock check | _check_sender_lock helper | ✓ WIRED | campaigns.py:485 → returns 409 SENDER_LOCK_CONFLICT |
| `CampaignEnqueueWorker.tick` | message_queue + cca | INSERTs with workspace_id+campaign_id propagation | ✓ WIRED | campaign_enqueue.py:181 INSERT message_queue, :172 calls rotation.get_or_assign_sender(commit=False) |
| `queue.py._tick` | campaigns table | INNER JOIN campaigns ON mq.campaign_id | ✓ WIRED | queue.py:184 JOIN + filter status='running'+start_date+IS NOT NULL |
| `queue.py._process_next_for_sender` | campaigns table | INNER JOIN (race-safety re-check) | ✓ WIRED | queue.py:302 same JOIN; LIMIT 8 candidate fall-through |
| `rotation.get_or_assign_sender` | campaign_senders pool | JOIN campaign_senders cs ON s.id=cs.sender_id WHERE cs.campaign_id | ✓ WIRED | rotation.py:113-117 |
| `send.py POST /api/v1/send` | campaign_id (workspace-scoped) → enqueue_message | explicit body field + JOIN check | ✓ WIRED | send.py:23 required campaign_id; :87 workspace-scoped FK SELECT; :181 enqueue_message(campaign_id=...) |
| `enqueue_message` | conversations.campaign_id + agent_id derivation | _upsert_conversation JOIN campaigns | ✓ WIRED | queue.py:810 derives ai_context_id from campaigns.agent_id; legacy fallback to extra_data |
| `ai_engine.generate_response` | campaign + agent context | get_context_for_conversation JOIN | ✓ WIRED | ai_engine.py:600 |
| `ai_engine.generate_response` | OpenAI tools list | build_builtin_tools(campaign) + build_tools(campaign.tools) | ✓ WIRED | ai_engine.py:638 CAMP-16 merge |
| `_handle_builtin_signal` | conversation.status update + webhook fire | UPDATE + notify_signal call | ✓ WIRED | ai_engine.py:218; webhook_notify.py:75 |
| `notify_signal` | fire-and-forget webhook | asyncio.create_task(_fire(url, payload)) | ✓ WIRED | webhook_notify.py:141 |
| `tests/conftest.py` | migrations/016_phase4.sql | exec_driver_sql after 015 | ✓ WIRED | conftest.py:67-68 |
| `agents.py campaign_count` | real SELECT (no longer hardcoded 0) | _count_campaigns_for_agent helper | ✓ WIRED | Closure of Phase 3 D-10 stub (TODO(phase-4) removed) |
| `agents.py DELETE` | 409 block on running campaign | _check_agent_not_in_running_campaign | ✓ WIRED | Closure of Phase 3 D-09 stub |
| `folders.py DELETE` | 409 block on running campaign | folder used by campaign check | ✓ WIRED | Phase 2 D-06 closure |
| `senders.py DELETE/PATCH` | 409 block on running campaign | _check_sender_not_in_running_campaign | ✓ WIRED | new Phase 4 check |

### Data-Flow Trace (Level 4)

Phase 4 produces backend API endpoints + background workers (no UI rendering — Lovable handles frontend). Level 4 applies to API → DB → API → Worker → External (Telegram + webhook) contract path:

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|-------------------|--------|
| `GET /api/v1/campaigns` campaigns list | `items` (List[CampaignResponse]) | `select(Campaign).where(workspace_id == ctx.workspace_id)` | Yes — real ORM query | ✓ FLOWING |
| `CampaignResponse.is_exhausted` | computed bool | SQL: folder contacts (tg_status='registered') vs cca rows + pending queue count | Yes — real SQL helper `_compute_is_exhausted` (campaigns.py) | ✓ FLOWING |
| `CampaignResponse.attached_senders[].locked_by_campaign_id` | computed UUID | SQL: senders shared with another running campaign | Yes — real SQL helper `_build_attached_senders` | ✓ FLOWING |
| `CampaignResponse.campaign_count` (on AgentResponse) | real INT | `_count_campaigns_for_agent(workspace_id, agent_id)` SELECT | Yes — closes Phase 3 D-10 stub | ✓ FLOWING |
| `POST /campaigns` AgentResponse | new campaign row | ORM add+commit+refresh | Yes — real INSERT | ✓ FLOWING |
| `CampaignEnqueueWorker.tick` → message_queue INSERTs | real DB rows | SELECT contacts + INSERT with rendered template_text | Yes — real per-tick population | ✓ FLOWING |
| `render_template` output | string with substituted vars | TEMPLATE_VAR_RE regex + RUSSIAN_ALIASES dict + contact.custom_fields | Yes — real string output (empty fallback per D-19 + warning log on missing var) | ✓ FLOWING |
| `_handle_builtin_signal` → conversations.status UPDATE | DB row update | UPDATE conversations SET status=… WHERE id=… | Yes — real UPDATE | ✓ FLOWING |
| `notify_signal` payload | dict POSTed to external URL | _fetch_history_excerpt + payload assembly | Yes — real fetch from messages table (auto-fix #1 in 04-05 — original plan referenced wrong messages_log) | ✓ FLOWING |

### Behavioral Spot-Checks

**Step 7b: SKIPPED (no runnable local environment)**

Local macOS env has Python 3.14 + SQLAlchemy 2.0.25 incompatibility + no Docker/Postgres. Inherited project-wide constraint from Phase 3 (documented in all 5 Phase 4 SUMMARY.md files Issues sections). Behavioral verification deferred to human run on DigitalOcean server (see human_verification section).

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| CAMP-01 | 04-02 | Создание кампании с именем и описанием | ✓ SATISFIED | POST /api/v1/campaigns; CampaignCreate {name, description}; UNIQUE INDEX (workspace_id, name) в migration 016 line 48-49 + 409 на дубль |
| CAMP-02 | 04-02 | Выбор агента-шаблона из списка workspace | ✓ SATISFIED | campaigns.agent_id FK REFERENCES ai_contexts(id) ON DELETE RESTRICT (migration 016 line 19); workspace-scoped Pydantic validation |
| CAMP-03 | 04-02 | Выбор папки контактов как таргета | ✓ SATISFIED | campaigns.folder_id FK REFERENCES folders(id) ON DELETE RESTRICT (migration 016 line 20) |
| CAMP-04 | 04-02 | Выбор TG-аккаунтов + sender блокируется за активной кампанией | ✓ SATISFIED | campaign_senders through-table (PK campaign_id+sender_id); _check_sender_lock в /start + /resume → 409 SENDER_LOCK_CONFLICT; attached_senders[].locked_by_campaign_id read-time computed |
| CAMP-05 | 04-02, 04-03 | Расписание кампании: рабочие часы и дни | ✓ SATISFIED | campaigns.{timezone, work_hour_start, work_hour_end, work_days_mask}; _campaign_in_working_window helper в queue.py:70 |
| CAMP-06 | 04-02, 04-03 | Старт/стоп даты (опционально) | ✓ SATISFIED | campaigns.{start_date, stop_date} NULLable; queue.py:184 WHERE `(start_date IS NULL OR NOW() >= start_date)`; past_stop_date soft-skip → status='failed', error_message='past_stop_date' |
| CAMP-07 | 04-02 | Статусы: draft/running/paused/done | ✓ SATISFIED | CHECK constraint в migration 016 line 40-41 (VARCHAR+CHECK per Q6 override) |
| CAMP-08 | 04-02 | Пользователь запускает/паузит/останавливает | ✓ SATISFIED | 4 lifecycle endpoints в campaigns.py: /start (line 485), /pause (:526), /resume (:547), /finish (:575) |
| CAMP-09 | 04-04 | Контакты досыпаются в активную кампанию через папку | ✓ SATISFIED | CampaignEnqueueWorker tick каждые 30s SELECT'ит folder contacts NOT IN cca → INSERT message_queue + cca; per-contact begin_nested() savepoint |
| CAMP-10 | 04-04 | Переменные `{{имя}}, {{username}}, {{source}}, {{custom.X}}` подставляются | ✓ SATISFIED | template.py:24 TEMPLATE_VAR_RE regex поддерживает `[a-zA-Zа-яА-Я_]+(\.[a-zA-Z_0-9]+)?`; :30 RUSSIAN_ALIASES dict {имя→name, юзернейм→username, телефон→phone, источник→source, компания→custom.company}; :49 alias resolution; missing var → empty string + warning (D-19) |
| CAMP-11 | 04-05 | Сигнал «передать лид» | ✓ SATISFIED | ai_engine.py:41 `mark_as_lead` ∈ BUILT_IN_TOOL_NAMES; _handle_builtin_signal UPDATE status='lead' + notify_signal('lead'); restrictive default description (Pitfall 7) |
| CAMP-12 | 04-05 | Сигнал «передать на менеджера» — заменяет auto_pause_triggers | ✓ SATISFIED | `transfer_to_manager` ∈ BUILT_IN_TOOL_NAMES; UPDATE status='handoff' + ai_enabled=false + paused_reason; notify_signal('handoff'); _handle_antispam_signal preserved параллельно (safety net) |
| CAMP-13 | 04-05 | Сигнал «финиш диалога» | ✓ SATISFIED | `finish_conversation` ∈ BUILT_IN_TOOL_NAMES; UPDATE status='finished' + ai_enabled=false; notify_signal('finish'); Q3 farewell: text_content возвращается перед status flip |
| CAMP-14 | 04-02 | 3 отдельных webhook URL (lead/handoff/finish), любой NULL = no-op | ✓ SATISFIED | campaigns table cols 31-33: lead_webhook_url, handoff_webhook_url, finish_webhook_url (все NULLable); webhook_notify.py:75 notify_signal — если URL NULL silent no-op (status update уже сделан caller'ом) |
| CAMP-15 | 04-05 | Tools кампании — спецификация function calling | ✓ SATISFIED | campaigns.tools JSONB DEFAULT '[]' (migration 016 line 37); ToolSpec Pydantic в schemas (мирорит recovered legacy webhook_functions shape); ai_engine custom-branch dispatch через build_tools(campaign.tools) |
| CAMP-16 | 04-05 | Сигналы + tools передаются в LLM-промпт вместе с агентским контекстом | ✓ SATISFIED | ai_engine.py:638 `all_tools = build_builtin_tools(campaign) + build_tools(campaign.tools)` — merged в один OpenAI call; built-in tools ВСЕГДА injected даже если campaign.tools=[] (D-12) |
| CAMP-17 | 04-04 | Очередь сообщений учитывает campaign_id — каждое сообщение принадлежит кампании | ✓ SATISFIED | message_queue.campaign_id NULLable column (migration 016 line 102-104, Q1 override); enqueue_message + enqueue_file сигнатуры accept campaign_id (B1); _upsert_conversation propagates campaign_id + derives agent_id via JOIN; queue worker JOINs campaigns + WHERE mq.campaign_id IS NOT NULL (defence-in-depth) |

**Orphaned requirements check:** REQUIREMENTS.md lines 164-180 (Traceability) map CAMP-01..17 to Phase 4 — all 17 covered. All declared in `requirements_completed` frontmatter of one of plans 04-01..04-05:
- 04-01: CAMP-15, CAMP-16, CAMP-17 (audit scope)
- 04-02: CAMP-01, CAMP-02, CAMP-03, CAMP-04, CAMP-07, CAMP-08, CAMP-14
- 04-03: CAMP-05, CAMP-06
- 04-04: CAMP-09, CAMP-10, CAMP-17
- 04-05: CAMP-11, CAMP-12, CAMP-13, CAMP-15, CAMP-16

No orphans. (Some IDs declared in 2 plans — e.g. CAMP-15/16 in 04-01 + 04-05 — это нормально: 04-01 audit recovered shape baseline, 04-05 wired implementation. CAMP-17 в 04-04 (campaign_id propagation) + 04-02 (column creation).)

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `app/main.py` | 72 | `version="2.0.0-phase3"` in FastAPI constructor (root endpoint at :106 correctly says "2.0.0-phase4") | ℹ️ Info | Cosmetic mismatch between FastAPI version meta и root response. Not a goal blocker — Lovable UI и n8n не зависят от FastAPI version metadata. Можно поправить в финальном Phase 4 commit или Phase 5. |
| (none) | — | TODO(phase-4) markers | — | **0 markers in app/ — B1 finalized per 04-05 SUMMARY checklist line 204.** All 10 markers from AUDIT.md Section 1 closed across 04-02 (4) + 04-04 (3) + 04-05 (3). |
| `app/services/listener.py` | 714 | document_webhook_url permanent comment (not restored) | ℹ️ Info | Per AUDIT.md item 6 + 04-05 SUMMARY decisions: НЕ восстанавливать document_webhook delivery — клиенты, которые хотят принимать файлы, определяют custom tool с file param в campaigns.tools. Intentional removal, not stub. |

**No blocker anti-patterns. No undocumented stubs.** Empirical rate-limit constants preserved per CLAUDE.md guard:
- `MIN_SEND_INTERVAL = 20` (line 38)
- `MAX_SEND_INTERVAL = 55` (line 39)
- `MAX_NEW_CONTACTS_PER_HOUR = 15` (line 49)
- `LONG_PAUSE_EVERY_MIN = 12` / `LONG_PAUSE_EVERY_MAX = 25` (line 55-56)
- `LONG_PAUSE_MIN_SECS = 180` / `LONG_PAUSE_MAX_SECS = 600` (line 57-58)
- `FLOOD_HARD_THRESHOLD = 300` (line 62)

`_handle_antispam_signal` preserved (listener.py:823 — antispam safety net параллельно с D-12 built-in tools, разные scopes).

### Human Verification Required

#### 1. Apply migration 016 + run full Phase 4 pytest suite on Docker/Postgres environment

**Test:**
```bash
cd /root/apps/outreach-platform
git pull
docker compose up -d --build api listener
docker compose exec api pytest \
  tests/test_migration_016.py \
  tests/test_campaigns_model.py \
  tests/test_campaign_router.py \
  tests/test_sender_lock.py \
  tests/test_campaign_schedule.py \
  tests/test_queue_per_campaign_hours.py \
  tests/test_template_render.py \
  tests/test_campaign_enqueue_worker.py \
  tests/test_queue_campaign_id.py \
  tests/test_send_campaign.py \
  tests/test_rotation_campaign.py \
  tests/test_builtin_tools.py \
  tests/test_campaign_webhooks.py \
  tests/test_custom_tools_wiring.py \
  -x -v
# + регрессия Phase 1-3:
docker compose exec api pytest tests/ -x -v --ignore=tests/test_<phase4>...
```

**Expected:**
- Migration 016 применяется чисто (idempotent — повторный прогон должен пройти без ошибок thanks to IF NOT EXISTS / DROP CONSTRAINT IF EXISTS).
- 14 test файлов (Phase 4) пройдут полностью — ~100+ тестов.
- Phase 1+2+3 регрессия зелёная (миграция 016 не сломала ничего из старых тестов).
- Конкретные acceptance criteria для каждого теста перечислены в `<acceptance_criteria>` блоках 04-02..04-05 PLAN.md.

**Why human:** Локальная среда (macOS Python 3.14 + SQLAlchemy 2.0.25 incompatible + нет Docker/Postgres) не может прогнать pytest. Это **known project-wide constraint** (documented в Issues всех 5 Phase 4 SUMMARY) — наследуется из Phase 3. Verification ограничена статическим анализом (grep + Read + AST). Реальное выполнение тестов — на DigitalOcean сервере или CI.

#### 2. Live end-to-end smoke: создание + старт + рассылка + signal → webhook

**Test:**
```bash
# Setup: Lovable UI / Postman, валидный JWT, тестовая workspace с активным sender'ом
# (Phase 2 onboarding) + folder с контактами (CSV import или Phase 2 push API) +
# agent (Phase 3 /api/v1/agents) + n8n webhook endpoint для тестового lead_webhook_url

# 1. Create campaign
curl -X POST http://localhost:8000/api/v1/campaigns \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Phase 4 Smoke",
    "agent_id": "$AGENT_ID",
    "folder_id": "$FOLDER_ID",
    "sender_ids": ["$SENDER_ID"],
    "message_template": "Привет, {{имя}}!",
    "lead_webhook_url": "https://your-n8n/webhook/lead",
    "timezone": "Europe/Moscow",
    "work_hour_start": 9,
    "work_hour_end": 20,
    "work_days_mask": 31
  }'
# Expected: 201 + CampaignResponse {id, status:"draft", is_exhausted: false, attached_senders:[{...locked_by_campaign_id: null}]}

# 2. Start campaign
curl -X POST http://localhost:8000/api/v1/campaigns/$CAMPAIGN_ID/start \
  -H "Authorization: Bearer $JWT"
# Expected: 200 + status:"running"

# 3. Wait 30+ seconds — CampaignEnqueueWorker tick должен прогнать
# SELECT contacts FROM folder WHERE tg_status='registered' AND NOT IN cca
# → INSERT message_queue + INSERT cca (per contact)
# Verify:
curl http://localhost:8000/api/v1/campaigns/$CAMPAIGN_ID -H "Authorization: Bearer $JWT"
# Expected: queue items > 0 (хотя поле не вьюжит — можно `docker compose exec db psql ...
# -c "SELECT COUNT(*) FROM message_queue WHERE campaign_id='...';"`)

# 4. Sender lock — пытаемся прикрепить тот же sender к другой running campaign
curl -X POST http://localhost:8000/api/v1/campaigns -d '{... same sender_ids ...}' -H "..." 
curl -X POST http://localhost:8000/api/v1/campaigns/$NEW_ID/start -H "..."
# Expected: 409 SENDER_LOCK_CONFLICT с [{sender_id, campaign_id, campaign_name}]

# 5. Trigger built-in signal — отправить контакту инициирующее сообщение
# (запустить отправку чем-то реальным через campaign queue), AI должен ответить
# и при правильном промпте/триггере дёрнуть finish_conversation tool. Альтернативно:
# вручную через psql:
# UPDATE conversations SET ai_enabled=true WHERE id='...' AND campaign_id='...';
# Отправить inbound сообщение через Telethon тест-аккаунт, listener должен:
# (a) дёрнуть ai_engine.generate_response → finish_conversation tool
# (b) UPDATE conversations SET status='finished', ai_enabled=false
# (c) POST на campaigns.finish_webhook_url с C-01 payload

# 6. Verify webhook fired
# Check n8n endpoint logs: payload должен содержать
# {event_type:"finish", campaign_id, campaign_name, conversation_id, workspace_id,
#  contact:{phone, name, username, source, custom, telegram_id}, reason,
#  message_history_excerpt:[...up to 20 msgs chronologically asc...], timestamp}

# 7. Pause campaign — queue worker должен SKIP'ать
curl -X POST http://localhost:8000/api/v1/campaigns/$CAMPAIGN_ID/pause -H "..."
# Verify: новые сообщения не отправляются (queue.py INNER JOIN + WHERE status='running' исключает)

# 8. Top-up — добавить новый контакт в folder во время active campaign
curl -X POST http://localhost:8000/api/v1/folders/$FOLDER_ID/contacts -d '{phones:[...new..]}' ...
curl -X POST http://localhost:8000/api/v1/campaigns/$CAMPAIGN_ID/resume -H "..."
# После следующего tick'а — новый контакт должен быть в queue
```

**Expected:** Все 8 шагов отвечают как specified. CampaignEnqueueWorker фактически прогоняет tick (логи `docker compose logs api | grep campaign_enqueue`). Webhook фактически срабатывает (n8n либо подобный test endpoint). Sender lock работает. Pause/resume не дают race-leak (item не уходит после pause). Top-up работает (CAMP-09).

**Why human:** Требует live FastAPI + Postgres + Telethon-session (реальные TG-аккаунты для отправки/получения) + Lovable workspace + n8n webhook endpoint для проверки C-01 payload shape. Эти end-to-end проверки доказывают Goal Achievement на runtime уровне (не только грэп). Также UI smoke (Lovable рендерит is_exhausted + attached_senders[].locked_by_campaign_id корректно).

### Deferred Items

Per `.planning/phases/04-campaigns/deferred-items.md` (deleted/closed during 04-04):

- **rotation.py raw-SQL refs to dropped `context_contact_assignments`** — RESOLVED in plan 04-04 (full rewrite).

**Phase 5+ carry-overs (per all 5 Phase 4 SUMMARY "Next Phase Readiness"):**

1. Inbox UI рендеринг новых статусов lead/handoff/finished — Phase 5.
2. Master bot для admin (ADMN-02) — payload shape C-01 готов, нужен только consumer — Phase 6.
3. v2 features: HMAC signature на webhook payload, sender_ids PATCH mutation (deliberately omitted в Phase 4 D-04), strict-mode `render_template` (currently empty fallback per D-19), POST `/{id}/senders` + `DELETE /{id}/senders/{sid}` dedicated endpoints, document_webhook_url restoration (NOT planned — alternative path = custom tool with file param).

### Gaps Summary

**No gaps blocking goal achievement.** All 6 ROADMAP success criteria satisfied at code level. All 17 CAMP requirement IDs marked Complete in REQUIREMENTS.md and traceable to specific code/migration artifacts. All 10 TODO(phase-4) markers (per 04-01 AUDIT inventory) are closed. Empirical rate-limit constants preserved per CLAUDE.md guard. Listener antispam handler preserved.

**One cosmetic-only finding:** `app/main.py:72` FastAPI(version="2.0.0-phase3") not bumped (root endpoint at :106 correctly returns "2.0.0-phase4"). Не блокирует ни одно из 6 success criteria, ни один из 17 CAMP requirements; рекомендация — обновить в очередном Phase 5 commit или сразу маленьким patch.

**Final blockers to "passed" status:** None at code level. Pytest suite must be run on Docker environment to satisfy the "tests pass" criterion from each PLAN (since AST/grep-only verification was used per inherited project constraint). Live smoke test should confirm runtime integration (CampaignEnqueueWorker tick дёргается, fire-and-forget webhook фактически POST'ит, signal dispatch UPDATE'ит DB).

Recommended next step: run the human_verification tests on the DigitalOcean server. If both pass → flip status to `passed`. If either fails → flip to `gaps_found` with specific gap details (and reopen plans accordingly).

---

_Verified: 2026-05-22_
_Verifier: Claude (gsd-verifier)_
_Mode: Initial verification (no previous VERIFICATION.md found)_
_Method: Static analysis (grep + Read + AST shape checks + migration SQL inspection) — environmental block on local pytest carried over from Phase 3_
