---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: executing
stopped_at: Completed 17-01-PLAN.md
last_updated: "2026-06-30T16:24:22.596Z"
last_activity: 2026-06-30
progress:
  total_phases: 19
  completed_phases: 16
  total_plans: 68
  completed_plans: 64
  percent: 92
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-05-21)

**Core value:** Клиент подключил аккаунт и через 10 минут первая кампания запущена — без программистов, без DevOps, без настройки серверов.
**Current focus:** Phase 17 — sender-side-resolve-ladder-with-username-capture-and-import-fallback

## Current Position

Phase: 17 (sender-side-resolve-ladder-with-username-capture-and-import-fallback) — EXECUTING
Plan: 2 of 4
Status: Ready to execute
Last activity: 2026-06-30

Progress: [█████████░] 92% (59/64 plans) — Phase 16: 1/5 plans (16-01 infra/data-model/test-scaffold complete)

## Performance Metrics

**Velocity:**

- Total plans completed: 22
- Average duration: —
- Total execution time: —

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 01 | 3 | - | - |
| 05.1 | 6 | - | - |
| 07 | 1 | - | - |
| 08 | 4 | - | - |
| 09 | 2 | - | - |
| 10 | 4 | - | - |
| 13 | 2 | - | - |

*Updated after each plan completion*
| Phase 02 P02-02 | 50min | 3 tasks | 13 files |
| Phase 02 P02-03 | 25min | 2 tasks | 3 files |
| Phase 02 P02-01 | 25min | 3 tasks | 8 files |
| Phase 02-tg-accounts-contacts P02-04 | 38min | 3 tasks | 6 files |
| Phase 02 P05 | 35min | 2 tasks | 6 files |
| Phase 03-agents-ai-templates P01 | 25min | 7 tasks | 14 files |
| Phase 03-agents-ai-templates P02 | 6min | 6 tasks | 7 files |
| Phase 04 P01 | 12min | 1 tasks | 1 files |
| Phase 04 P02 | 75min | 3 tasks | 14 files |
| Phase 04 P03 | 6min | 2 tasks | 3 files |
| Phase 04 P04 | 10min | 5 tasks | 13 files |
| Phase 04 P05 | 9min | 3 tasks | 8 files |
| Phase 05 P01 | 13min | 3 tasks | 12 files |
| Phase 05 P02 | 5min | 2 tasks | 5 files |
| Phase 05 P03 | 6min | 3 tasks | 7 files |
| Phase 07-unified-freeze-policy P01 | 14min | 3 tasks | 4 files |
| Phase 08 P01 | 9min | 3 tasks | 3 files |
| Phase 08 P02 | 8min | 1 tasks | 1 files |
| Phase 08-pool-management-and-even-distribution P03 | 16min | 3 tasks | 3 files |
| Phase 08-pool-management-and-even-distribution P04 | ~3h | 4 tasks | 7 files |
| Phase 10 P01 | 20min | 2 tasks | 2 files |
| Phase 10 P02 | 25min | 3 tasks | 6 files |
| Phase 10 P03 | 5min | 3 tasks | 3 files |
| Phase 10 P04 | 12min | 1 tasks | 5 files |
| Phase 11 P01 | 16min | 3 tasks | 5 files |
| Phase 11 P02 | 34min | 3 tasks | 17 files |
| Phase 11 P03 | 25min | 3 tasks | 2 files |
| Phase 13 P01 | 6min | 2 tasks | 1 files |
| Phase 13 P02 | 8min | 3 tasks | 2 files |
| Phase 16 P01 | 12min | 3 tasks | 11 files |
| Phase 16 P02 | 8min | 2 tasks | 4 files |
| Phase 16 P03 | 12min | 3 tasks | 7 files |
| Phase 16 P04 | 7min | 2 tasks | 2 files |
| Phase 17 P01 | 14min | 3 tasks | 4 files |

## Accumulated Context

### Decisions

See full log: PROJECT.md → Key Decisions

- Auth: Magic link via Supabase (нативно в Lovable)
- **Campaign как первичная сущность** (объект-обёртка над рассылкой со статусом, расписанием, сигналами)
- **Agent отвязан от sender'а** — workspace-level AI-шаблон, переиспользуется между кампаниями
- **Webhook + tools на уровне кампании, не агента** (агент = как говорить, кампания = куда передавать данные)
- **Сигналы (лид/менеджер/финиш) на уровне кампании**, передаются в LLM-промпт вместе с агентским контекстом
- **Rate limits per-sender** (Telegram anti-spam смотрит на аккаунт), **расписание per-campaign** (бизнес-параметр рассылки)
- **Папки в базе контактов** — таргет кампании
- API: полный рерайт — старые эндпоинты остаются в telegram-api (prod), пишем новые с нуля
- Brownfield: бизнес-логика не трогается, добавляем workspace_id + campaign-модель поверх
- [Phase 02]: Plan 02-02 closes SNDR-01..03: derived status (D-11), rate-limit warnings (D-14), per-sender DB-stored rate_per_min/hour/day (D-13), assign-proxy + workspace proxy CRUD (D-22). Migration 013 drops senders.is_active; all 14 hidden call-sites swept across listener/warmup/rotation/health/queue/onboarding.
- [Phase 02]: Plan 02-01 closes ONBD-01..05: workspace-scoped onboarding rewrite onto AuthCtx, persistent state in onboarding_sessions (D-16/D-17), listener.reconcile_loop replaces subprocess.run docker-restart (D-18); host socket mount and user:root removed from api service.
- [Phase 02-tg-accounts-contacts]: Contacts API: two-step CSV import (preview→apply, 30-min BYTEA TTL), per-record ON CONFLICT dedup, FLDR-03 folder_name auto-create via get_or_create_by_name reuse
- [Phase 02-tg-accounts-contacts]: D-20 has_checker check decided at INSERT time: tg_status='unchecked' fallback when workspace has no checker; plan 02-05 ContactCheckWorker filters WHERE tg_status='pending'
- [Phase 02-tg-accounts-contacts]: Phone normalization: pure regex E.164 (no phonenumbers lib) + RU leading-8 heuristic gated by 11-digit + no leading +
- [Phase 02]: Plan 02-05: ContactCheckWorker reuses CheckerService (no FloodWait/polite-delay duplication); JOIN LATERAL gates workspace isolation; recheck endpoint is workspace-scoped 202 Accepted; has_checker exposed for D-20 UI banner.
- [Phase 03-agents-ai-templates]: Phase 3 plan 01: migration 015 — DROP 6 ai_contexts columns + senders.ai_context_id + UNIQUE(workspace_id, name); ORM AIContext reduced to D-02 fields; 5 worker-services adapted (ai_engine/listener/rotation/queue/senders router); 7 TODO(phase-4) markers left for Campaign-level reconnection
- [Phase 03-agents-ai-templates]: Phase 3 plan 02: workspace-scoped /api/v1/agents (6 endpoints) + /api/v1/send rewrite under AuthDep with explicit ai_context_id (D-06); hard delete via FK cascades (D-08); duplicate auto-name with retry-on-IntegrityError (Pitfall 2); campaign_count=0 hardcoded (D-10); legacy contexts.py deleted, send-file/send-batch dropped (С-04)
- [Phase 04]: Phase 4 Plan 01 (audit): Q1 message_queue.campaign_id NULLable + ON DELETE SET NULL (overrides CONTEXT.md D-16); Q6 campaigns.status VARCHAR(20)+CHECK (overrides D-04 SQLEnum) — PG ALTER TYPE ADD VALUE cannot run in transaction; webhook_functions internal shape recovered from init commit 54430ec (param array, not JSON Schema); 10 TODO(phase-4) markers inventoried with closure plan per marker
- [Phase 04]: Plan 04-02: campaigns.status VARCHAR+CHECK (Q6 override) — ALTER TYPE ADD VALUE blocks transactions; message_queue.campaign_id NULLable + SET NULL (Q1 override) — preserves queue history on hard delete of done campaigns; lifecycle as explicit POST endpoints; computed is_exhausted + attached_senders.locked_by_campaign_id at GET time; rotation.py reference to dropped context_contact_assignments deferred to 04-04 per AUDIT TODO #6
- [Phase 04]: Plan 04-03: per-campaign scheduling — выпилены MOSCOW_TZ/WORK_HOUR_*/_is_working_hours/_next_working_window из queue.py; добавлен _campaign_in_working_window(tz, h_start, h_end, days_mask) helper; _tick + _process_next_for_sender JOIN на campaigns с фильтром status='running' + start_date/stop_date window + work hours (Python-side post-filter); past stop_date items → failed/past_stop_date (D-11); H4: explicit mq.campaign_id IS NOT NULL defence-in-depth; эмпирические rate-limit константы untouched (CLAUDE.md guard)
- [Phase 04]: Plan 04-04: render_template Mustache regex with RU aliases (имя/юзернейм/телефон/источник/компания) + empty fallback (D-19); rotation.py rewritten with commit=False kwarg (M2) для worker savepoint; CampaignEnqueueWorker singleton + lifespan; enqueue_file accepts campaign_id (B1 file-flow синхронизирован с message-flow); 3 TODO(phase-4) markers закрыты (queue.py:705, queue.py:849, rotation.py); empirical constants untouched (CLAUDE.md guard)
- [Phase 04]: Plan 04-05: built-in OpenAI function tools (mark_as_lead/transfer_to_manager/finish_conversation per C-04) ВСЕГДА инжектятся даже когда campaigns.tools=[] (D-12); restrictive default descriptions (Pitfall 7) — Use ONLY/Do not mark — снижают false-positive over-triggering на casual greetings; priority dispatch (Pitfall 1): _BUILTIN_PRIORITY = {finish:0, handoff:1, lead:2}, sorted descending → последний UPDATE = highest-priority; Q3 farewell semantic — text_content возвращается перед status flip когда finish/handoff parallel с text (без second LLM call для tool-result summary); M3 legacy fallback — campaign_id NULL → ai_context_id direct path, get_context_for_conversation НЕ raises; custom tools источник = campaigns.tools JSONB (D-14), webhook_functions путь mortuus; no HMAC на webhook payload (deferred v2); _handle_antispam_signal preserved as safety net; document_webhook_url НЕ восстановлен (custom tool с file param)
- [Phase 04]: Phase 4 B1 finalized: 0 TODO(phase-4) markers в app/ — все 10 AUDIT.md Section 1 markers закрыты (agents.py:49+246, folders.py:248, queue.py:708+849, rotation.py:180, ai_engine.py:88, listener.py:250+350+707). Phase 4 готов к verification.
- [Phase 05]: Plan 05-01: migration 017 defensive messages CREATE TABLE (DDL lost in brownfield fork — IF NOT EXISTS no-op on prod); ANTISPAM_BOT_IDS at module level for D-08 delegation from new bot filter; D-03 fix — enable-ai NEVER touches status; pre-send guard in queue.py one extra SELECT (CLAUDE.md empirical intervals untouched)
- [Phase 05]: [Phase 05]: Plan 05-02: analytics endpoints — sent source = messages JOIN conversations (C-01 covers manager-send D-04 unlike messages_log/message_queue); replied = one SELECT with COUNT(DISTINCT) + COUNT(*) per D-15; _ALLOWED_SCOPE_COLUMNS whitelist + :scope_val bind for safe scope composition; Pitfall 8 — bot_ignored excluded from every COUNT; Pitfall 9 — leads strict EQ; D-13 — no background workers added (lifespan still 5)
- [Phase 05]: Plan 05-03: inline await log_llm_call (Open Question #3) — deterministic + testable; +1-3ms latency acceptable for v1; D-12 preserved (warmup.py has 0 references); T-05-03-PROMPT-LEAK guard verified via grep (0 matches for logger.*prompt in llm_logger.py + ai_engine.py); defence-in-depth on GET /llm-calls endpoint (prequery + WHERE workspace_id); Phase 5 complete (3 plans, ANLX-05 closed alongside INBX-01..05 + AIRC-04 + ANLX-01..04)
- [Phase 07-unified-freeze-policy]: Phase 07 Plan 01: antispam path converged onto PEER_FLOOD soft-restriction (pause pending +24h, flag spam_limited) instead of terminal-fail; ai_enabled block deleted (replies keep flowing); rotation excludes restriction_status != 'none'. Decisions: pause scoped to status='pending' only (avoids in-flight race), AND restriction_status <> 'frozen' guard (frozen-precedence). NO migration (028 pre-existing). 21/21 targeted tests green.
- [Phase 08]: Plan 08-01: Wave-0 test scaffold — test_queue_item_factory (message_queue + sticky CCA + optional conversation) + 10 fully-asserting RED tests (test_pool_endpoints POOL-01..06b + test_rebalance POOL-07/08/08b); rebalance import inside test body keeps --collect-only clean. 683 collected / 0 errors.
- [Phase 08]: Plan 08-02: rebalance_on_attach campaign-scoped even-split (rebalance.py) — eligible-pool filter copied from rotation.py:113-123, floor-target back-fill of new sender only (BATCH_CAP=500), donor rows FOR UPDATE OF mq SKIP LOCKED + status='pending' (no worker race), queue.sender_id + CCA.sender_id in one TX (lock-step); _pick_least_loaded NOT reused (global scope); no migration. POOL-07/08/08b GREEN.
- [Phase 08-pool-management-and-even-distribution]: Plan 08-03: attach/detach pool endpoints on the existing campaigns router — attach reuses _validate_workspace_owns_senders + _check_sender_lock with the /start 409 SENDER_LOCK_CONFLICT contract (insert→flush→check→rollback), allowed on draft/paused/running (D-01), rebalance_on_attach gated to running (D-08); detach guards MIN_POOL_GUARD (running+last) + DETACH_BLOCKED_PENDING (cold-pending, engaged dialogs excluded via NOT EXISTS conversations, D-05), no auto-reassign (D-06). CampaignSenderAttach gained computed id; pool tests given per-test JWT subs to dodge user_workspaces UNIQUE binding. POOL-01..06b GREEN.
- [Phase 08-pool-management-and-even-distribution]: Plan 08-04 (POOL-09, cross-repo, human-verify): interactive Senders/Пул panel in sibling aimly-tg-outreach (attachMut/detachMut mirror lifecycleMut + invalidateQueries(['campaign', id]), multiselect/chips add, per-row remove, locked display, human-readable 409s via existing actionError banner; D-10/D-11/D-12). error-codes.ts: SENDER_LOCK_CONFLICT rewritten to array-based detail.conflicts[].campaign_name + new MIN_POOL_GUARD/DETACH_BLOCKED_PENDING. openapi.json + types regenerated via export-handoff (no hand-edit). UAT-driven addition: **GET /api/v1/senders now exposes locked_by_campaign_id/name** so the add-picker disables locked-by-running-campaign senders instead of offering them then 409-ing — **D-02 lock semantics UNCHANGED, only the existing lock is surfaced**. Cross-repo reconcile: panel rebased onto origin/main over 16 concurrent Lovable commits (sibling cfefc62), no Lovable commit dropped. Backend pool tests 8/8 GREEN via test-overlay; sibling tsc clean. Phase 08 complete (4/4).
- [Phase 10]: 10-02: durable append-only sender_restriction_events (HLTH-01/02) via dual-mode record_restriction_event helper; 5 account write-points + recipient_privacy in-TX; D-01 forward-shift gate in the helper; OQ#1 flood_wait informational, OQ#2 source=antispam_signal, OQ#3 PRIVACY_RESTRICTED mandatory; B-1 old_until read intra-transaction
- [Phase 10]: 10-03: pool_health one-pass aggregate (COUNT FILTER + MIN FILTER) + per-sender restriction enrichment on CampaignResponse (POOLV-01/02); GET /senders/{slug}/restriction-events workspace-scoped newest-first (HLTH-03); API presentation-free, badge derived on frontend; earliest_resume_at=MIN(restricted_until) (OQ#4)
- [Phase 10]: 10-04: 3-state pool badge derived on frontend from numeric pool_health (green/yellow/red); OQ#4 wording 'до проверки в T'; per-sender restriction chips on attached pool (POOLV-02); account-page restriction-event mini-list off HLTH-03 endpoint in a slug-keyed modal (POOLV-04). openapi/types regenerated via export-handoff (cross-repo: openapi→backend, components→sibling). Task 2 human-UAT PENDING.
- [Phase 11]: Migration slot 031 already taken by 031_sre_flood_wait_category.sql; Phase 11 uses 032_phase11_field_split.sql
- [Phase 11 P02]: D-01 tone_preset replaces voice_baseline/tone/tone_of_voice; D-11 response_speed/response_delay_seconds added; D-04/D-12/D-14 dialogue_flow/arguments_facts/campaign_rules on campaigns; D-13 success_criteria merged into lead_trigger_hint before DROP; migration 032 idempotent; tests 651 passing (up from 456 pre-Phase-11)
- [Phase 11 P03]: D-03 _TONE_LINES single-source tone; D-06 dialogue_flow numbered stages replace static _PROMPT_DIALOGUE_GOAL; D-12 _PROMPT_FACTS_GUARD anti-hallucination in arguments_facts block; D-14 _dedup_rules(agent_rules, campaign_rules) exact-duplicate suppression; D-11/RT-01 response_speed instant/human/slow/manual → listener debounce delay branch; PMT-01..07 + RT-01 all GREEN (660 passing)
- [Phase 11 P04]: openapi.json+types regenerated via export-handoff (rebuilt API container first — was stale pre-P11); frontend Agent form: tone_preset select (4 opts) replaces voice_baseline+sliders+tone_of_voice (all deleted); response_speed select + conditional delay input; Campaign wizard: StageEditor (add/remove/up-down, no dnd lib, aria-labels, empty-state copy), arguments_facts+campaign_rules textareas, audience_hints→Кому пишем relabel, success_criteria field removed→merged into leadHint (Сигнал Лид), autoFillMut→lead_trigger_hint; EditCampaignModal updated with InlineStageEditor+new fields; tsc clean; awaiting human UAT (Task 4)
- [Phase 13]: 13-01: Wave-0 RED scaffold tests/test_queue_even_pacing.py (7 tests, PACE-01..07) reuses Phase 12 helpers verbatim; deferred in-body imports keep --collect-only clean; _assert_pacing_predicate_wired() introspection guard (binds :expected_now/:window_start_utc) makes the four behavioural integration tests genuinely RED instead of coincidentally passing on the Phase 12 cap. All 7 RED, 16 pre-existing queue tests stay GREEN.
- [Phase 13]: 13-02: even pacing implemented in queue.py only (D-09) — _window_elapsed_fraction (raw window D-01, window_start floor D-06, clamped, injectable now) + expected_now = cap*frac*jitter (D-05/D-08) ANDed beside the Phase 12 cap in the candidate SELECT; follow-ups bypass (D-07/D-10), no max() clamp (structural floor D-03), LIMIT 8 / SKIP LOCKED preserved. PACE-01..07 GREEN, full suite 756 passed. Phase 12 test_new_dialog_allowed_under_cap re-seeded 23h-ago (cap vs pace two-counter isolation, assertion unchanged).
- [Phase 16]: 16-01: pgvector stood up (prod+test images pgvector/pgvector:pg16, command: block preserved); migration 041 (4 KB tables + HNSW vector_cosine_ops + btree, idempotent); ORM mirror incl. Vector(1536) so create_all builds same test schema; conftest CREATE EXTENSION vector before create_all + 041 apply guard; AIContext.knowledge_base untouched (D-08). HNSW chosen (builds on empty table); pasted text in raw_content BYTEA source_kind='text' (OQ1); STORAGE=SUM(size_bytes) (OQ2). 10-test RED scaffold for KB-01..KB-06, full suite 837 collected 0 errors.
- [Phase 16]: 16-02: kb_ingest pipeline — tiktoken cl100k_base ~800/120 sliding-window chunking, multi-format extract (pdf/docx/txt/md/csv/text) off the event loop via asyncio.to_thread, embed_texts reuses ai_engine AsyncOpenAI client batched ≤256 (Pitfall 6). KnowledgeIngestWorker mirrors ContactCheckWorker: claim pending FOR UPDATE SKIP LOCKED → processing (committed so UI polls) → parse/chunk/embed → indexed/failed; delete-then-insert kb_chunks for idempotent re-index (Pitfall 8); empty doc → indexed chunk_count=0; never-dies loop. Registered in lifespan next to contact_check_worker. KB-02/KB-03 worker tests GREEN; partial-staged main.py KB hunks (left Phase-15 CORS edit unstaged).
- [Phase 16]: Plan 16-03: workspace-scoped KB API (CRUD + upload/paste 202 → pending row + D-09 COUNT(*) FILTER aggregate + manual cosine search + agent M:N attach/detach + reverse list) under auth_dep; list endpoints return bare arrays (RED-test contract); openapi regenerated offline via app.openapi() (no un-gated prod deploy); router partial-staged in main.py leaving Phase-15 CORS hunk unstaged. KB-01/02/03/04 GREEN.
- [Phase 16]: kb_search signature is (db, workspace_id, kb_ids, query, top_k, max_distance) — matches the 16-03 router import + RED tests; agent→kb_ids resolution lives in attached_kb_ids
- [Phase 16]: search_knowledge_base is a DATA tool (NOT in BUILT_IN_TOOL_NAMES) — gated on ≥1 attached KB (D-04), resolved locally, two-pass continuation, never touches conversation.status
- [Phase 17]: 17-01: Wave-0 RED scaffold — 12 SRLD tests (10 RED, 2 GREEN contracts) across test_checker/test_send/test_contact_check_worker/test_restriction_audit. SRLD-07 cache-gate uses async_db_session (COMMIT — _lookup_cache/_get_cached_contact open own AsyncSessionLocal); confidence signal read off contacts.tg_probe_state/tg_confidence (contacts_cache has no source col, D-12 reuse). client.calls request-type introspection asserts the resolve-ladder shape (ResolvePhone removed, ResolveUsername/Import fire). SRLD-02 is a GREEN persistence contract (worker:875 already writes tg_username_resolved; pins CSV-vs-resolve provenance). Full suite 852 collected, 0 errors. No app/ touched.

### Roadmap Evolution

- Phase 05.1 inserted after Phase 5: Lovable UI v1 — auth + onboarding + TG accounts + contacts + agents + campaigns + inbox + analytics + settings (URGENT — closes Core Value + 7 HUMAN-UAT items from Phase 5)
- Phases 7–10 added (2026-06-22): post-v1 block "Sender Pool Resilience & Failover" — design in `.planning/proposals/sender-pool-resilience.md`. P7 Unified Freeze Policy, P8 Pool Management & Even Distribution, P9 Cold-Contact Failover, P10 Pool Visibility (optional). Triggered by campaign b7cc7d06 antispam-stall incident (quick 260622-j52).
- Phase 11 added (2026-06-24): Agent/Campaign Field Split & Prompt Assembly — развести слои Агент(КТО)/Кампания(ЧТО), убрать дубли в системном промпте (один источник на блок), новые поля (скорость ответа, ход разговора, аргументы и факты, базы знаний) + перестройка UI визарда. Полный бриф: `.planning/phases/11-agent-campaign-field-split-and-prompt-assembly/BRIEF.md`.
- Phase 12 added (2026-06-25): Per-campaign daily new-dialog limit (`max_new_dialogs_per_day`) — явный настраиваемый дневной лимит новых холодных диалогов на уровне кампании (default 50, soft-cap >50 → warning, hard cap 100 → 422). Enforcement в `_check_rate_limits` по уникальным новым диалогам за trailing-24h; фоллоу-апы не блокируются. Закрывает отсутствие лимита на холодные диалоги (сейчас только per-sender 150/день).
- Phase 13 added (2026-06-25): Even pacing across sending window — равномерное распределение новых диалогов по активному окну (`max_new_dialogs_per_day / активные_часы → целевой интервал`), батчинг пула, 1 диалог каждые 3–5 мин с дрожанием. Depends on Phase 12. Выделено из обсуждения Phase 12 (pacing — отдельный механизм от жёсткого потолка, трогает защищённые эмпирические константы queue.py).
- Phase 14 added (2026-06-26): Reliable Contact Resolution — надёжная/масштабируемая проверка контактов в TG (health-probe на заведомо-живых, burst-кап+cooldown, пул чекеров с ротацией, перепроверка контаминированных данных, confidence/source на not_registered, фикс дыры в `contact_check_worker`). Триггер: расследование во время /gsd-explore — единственный checker `sender-8428118140` теневно ограничен contacts-API и занижал живых в ~15–20 раз (2.5% vs ~26%). Часть 1 (пауза чекера, чистка 2216 кэша, 2110 контактов → pending) выполнена вручную. Диагноз+калибровка: `.planning/notes/checker-false-negatives.md`. Requirements RESV-01..07.
- Phase 15 added (2026-06-29): Account Warmup via Inter-Account AI Chat — продуктизация взаимного AI-прогрева (аккаунты workspace переписываются между собой через AI для безопасного набора активности/возраста) + отдельная UI-вкладка (старт/стоп, расписание, интенсивность, статус). **Ключевое требование — изоляция от основного флоу аутрича** (не перехватывать реальные диалоги кампаний, не садить лимиты sender'ов, не триггерить AI-ответчик). Базовый движок уже есть: `app/services/warmup.py` + `app/routers/warmup.py`. Референс-прототип (остановлен т.к. влиял на основной флоу): `/root/apps/telegram-api/app/services/warmup.py` + `routers/warmup.py` + `bot_chat.py`. Requirements TBD (определить в discuss-phase).
- Phase 17 added (2026-06-30): Sender-side resolve ladder with username capture and import fallback — **отправитель сам резолвит и дотягивается**, **чекер = чистый фильтр** «есть/нет» + захват `@username` (публичный/переносимый, в отличие от per-account `access_hash`). Тройная лестница резолва на отправителе: (1) кэш per-sender → (2) `ResolveUsername` по захваченному чекером @username (дёшево/безопасно, обходит приватность по телефону, не засоряет книгу) → (3) `ImportContacts` лениво по одному перед отправкой (не пачкой — burst у ~47–49; 4/мин размазывает). Чужой `access_hash` не переиспользуется; фолбэк на phone-резолв; кэш не чистим. Триггер — живой инцидент «Barter - ВЭД хук»: 22 живых РФ-номера терминально упали на `ResolvePhone` (флаг ставил US-аккаунт — чужой резолв не переносится + US врёт на РФ; собственный ResolvePhone отправителя дал ложное «нет»; в send-пути нет import-фолбэка). Depends on Phase 14. Дизайн-документ: `.planning/notes/sender-side-resolve-redesign.md`. Requirements TBD (derived в /gsd:plan-phase 17).
- Phase 16 added (2026-06-30): RAG Knowledge Bases for Agents — RAG-база знаний для AI-агентов. Логика: (1) отдельная UI-вкладка **Knowledge Bases**, где пользователь создаёт изолированные KB и загружает в каждую данные; (2) KB подключается **на уровне агента**, который ходит в неё по необходимости (retrieval при генерации). KB workspace-scoped, связь M:N агент↔KB. Предв. scope: модель данных (`knowledge_bases`/`kb_documents`/`kb_chunks`/`agent_knowledge_bases`), ingest-пайплайн (парсинг→чанкинг→эмбеддинги), хранилище векторов (предпочтительно pgvector в существующем PostgreSQL — решить D-NN), retrieval в listener/AI-сервисе. Depends on Phase 3 (Agents/AIContext — точка привязки) + Phase 1 (Workspace scoping). Requirements TBD (derived в /gsd:plan-phase 16).

### Pending Todos

None yet.

### Blockers/Concerns

- Phase 4 первым планом — аудит существующего webhook + function calling (вынести с уровня sender/AIContext на уровень кампании)
- rotation.py:59,89,122,138 still references DROPPED context_contact_assignments table — 04-04 must rewrite per AUDIT TODO #6 (context_id → campaign_id signature)
- **✅ RESOLVED — Phase 14 CLOSED 2026-06-30 (superseded).** Защитная механика чекера построена и задеплоена (14-01/02/03/05/06/07 + b7j/mig-036 + trip_count-reset фикс); SC #1/#2/#5 закрыты. SC #3/#4 (re-activation пула + перечек 2110+699+14k) НЕ закрыты намеренно — нужны genuinely-fresh warmed RU чекеры, которых нет; остаток передан в **Phase 17** (sender-side resolve ladder, снимает зависимость от отдельного пула). Решение пользователя: «ушли дальше, неактуально». Записи ниже — историческая хронология, действий не требуют (устаревший «Next: /gsd:execute-phase 14» отменён).
- **14-04 live smoke FAILED → Phase 14 needs gap-closure (2026-06-26).** Waves 1-3 merged + DEPLOYED (mig 034 applied, probe_checker/resolve_phone_with_fallback/tg_probe_state confirmed in running container; 768 tests GREEN). Re-activated the 2 "healthy" checkers (sender-7979031303/8364639216) under the deployed guards — they STILL produced the throttle signature: `checked=20..30 reg=0 flood=True` = 0% mobile-registered (calibration expects ~50%). **Code gap:** on `flood=True` the worker finalized empty results as `tg_status='not_registered'` with `tg_confidence='high'`/`tg_probe_state='clean'`; control-probe fired NO `sender_restriction_events`, suspect-rollback never engaged. Prod fully rolled back (UPDATE 50 → pending, DELETE 50 false cache; 49 control intact; baseline not_registered=5/pending=14484/registered=53; 0 provenance). All 3 checkers parked, api restarted, worker idle. Gap-closure scope in `.planning/notes/checker-false-negatives.md` §"Часть 2": (1) flood/throttle-aware finalization (never trust flood resolve as not_registered; mark checker restricted + pull from rotation); (2) investigate whether throttle is pool-wide (long cooldown? @username-only resolve?). Docs RESV-07 done (CLAUDE.md commit 9669e7c).
  - **GAP-CLOSURE PLANNED (2026-06-26, commit c7ff169):** 2 new plans, plan-checker VERIFICATION PASSED (no blockers/warnings). **14-05** (Wave 5, autonomous) — Gap A: inline flood/throttle-aware finalization (flood OR anomalous all-empty batch → roll to `pending`, never `not_registered`/`high`/`clean`; degrade checker inline via `_flag_checker_degraded` + `sender_restriction_events` + cooldown; N=0-healthy safe-stop; RED-first tests; no new migration). **14-06** (Wave 6, autonomous:false, blocking human-verify) — Gap B: read-only diagnostic spike → findings note `.planning/notes/checker-pool-throttle-spike.md` answering phone-resolve-dead-pool-wide? / @username-viable? / our-rate-triggers-throttle?, with GO/NO-GO at the gate. **DEFERRED:** RESV-04 full re-check + re-activation of the 14k drain → follow-up only after the spike's GO verdict. Next: `/gsd:execute-phase 14`.
  - **GATE OVERRIDE (2026-06-26):** decision-coverage-plan gate reported 2/11 (D-01/02/03/05/06/08/09/10/11 "uncovered") — known false-positive (gate matches only verbatim `D-NN:` in must_haves/truths, can't soft-match Russian; see memory gsd-decision-coverage-gate-cyrillic). Substantively covered: D-01/02/03/05/06/09 by EXECUTED 14-01..14-04; D-08 = RESV-04 deliberately deferred. User chose "Proceed anyway". Re-surface at verify-phase if relevant.

### Quick Tasks Completed

| # | Description | Date | Commit | Directory |
|---|-------------|------|--------|-----------|
| 260618-a97 | Разрешить сохранять неполный draft кампании (agent_id/folder_id/message_template опциональны; обязательность — на /start) | 2026-06-18 | 1b04e9c | [260618-a97-draft-agent-id-folder-id-message-templat](./quick/260618-a97-draft-agent-id-folder-id-message-templat/) |
| 260618-e9r | Анти-AI-слоп паттерн в дефолтном `<message_style>`: типографика, рус/англ запрещённая лексика, лимит «однако»/«например», естественность | 2026-06-18 | 0c84389 | [260618-e9r-anti-ai-slop-message-style](./quick/260618-e9r-anti-ai-slop-message-style/) |
| 260618-r7k | Per-campaign re-contact policy: флаг `allow_recontact` + `recontact_min_age_days` + триггер свежести `updated_at`; закрытые/старые диалоги снова eligible, fresh-start новой строкой, детерминированный роутинг входящих | 2026-06-18 | 08a1c5a | [260618-r7k-campaign-allow-recontact](./quick/260618-r7k-campaign-allow-recontact/) |
| 260619-bdm | Bulk delete + move контактов: `POST /contacts/delete` (batch), UI multi-select toolbar (Move to…/Delete/Clear) во фронте. Попутно — drift-fix `folders(workspace_id,name)` UNIQUE (mig 027) + conftest 019–027 (−85 red tests) | 2026-06-19 | ddceca9 | [260619-bdm-contacts-bulk-delete-move](./quick/260619-bdm-contacts-bulk-delete-move/) |
| 260619-frz | Sender write-restriction (spam-limit / freeze): новые колонки `restriction_status`/`restricted_until` (mig 028); `_derive_status` отдаёт `limited`/`frozen`; детект `FROZEN_*`→`ACCOUNT_FROZEN` в telegram.py; PEER_FLOOD/FROZEN флагают sender в queue + pre-send skip; фоновый SpamBot-reconcile sweep в listener (free=снять+un-pause, limited=продлить, suspended=ban); фикс бага spambot-check (писал несуществующий auth_status='limited'). 8 новых тестов | 2026-06-19 | 0f84870..5f4f944 | [260619-frz-sender-restriction-status](./quick/260619-frz-sender-restriction-status/) |
| 260622-gxt | SpamBot self-check antispam guard: наш собственный пинг @SpamBot (reconcile sweep / ручной spambot-check) больше не убивает очередь sender'а. In-memory реестр `TelegramService._spambot_selfcheck` (mark/is + TTL-prune); `check_spambot(client, selfcheck_key)` помечает окно перед `/start`; guard в начале `_handle_antispam_signal` пропускает solicited-ответ (обе ветки детекта). Только in-memory → sweep (тот же процесс listener) покрыт полностью; ручной endpoint в api-процессе НЕ покрыт (задокументировано). 4 новых теста | 2026-06-22 | 7da5f6e..f796893 | [260622-gxt-spambot-selfcheck-antispam-guard](./quick/260622-gxt-spambot-selfcheck-antispam-guard/) |
| 260622-j52 | Requeue 37 antispam-auto-cancelled контактов в кампании b7cc7d06 (ops, без кода): все 37 отменены одним antispam-событием 2026-06-19 13:07:55 (attempts=NULL). Бэкап → транзакция с count-guard (target_rows=37) → `UPDATE 37` обратно в pending. Аккаунт здоров (restriction_status=none). Итог: pending=37/sent=9/failed=2 (PEER_FLOOD+PRIVACY не тронуты). Воркер подхватил очередь за ~15с, темп ≤4/мин | 2026-06-22 | _ops_ | [260622-j52-requeue-37-antispam-auto-cancelled-conta](./quick/260622-j52-requeue-37-antispam-auto-cancelled-conta/) |
| 260623-ff1 | Документация семантики checker'а: `is_registered=false` = «не резолвится по телефону сторонним аккаунтом», НЕ «нет Telegram-аккаунта» (приватность find-by-phone даёт ложноотрицания). Проверено 2026-06-23 (checker `sender-8428118140` здоров: бросил PhoneNotOccupied на наши собственные приватные senders, при этом 83 номера is_registered=true). Caveat в docstring/inline-комментах `checker.py` + рус. подсекция в `CLAUDE.md`. Только docs — AST без изменений, без rename/миграции | 2026-06-23 | 2050f59..732d8da | [260623-ff1-document-checker-semantics-phonenotoccup](./quick/260623-ff1-document-checker-semantics-phonenotoccup/) |
| 260629-kn4 | Контакты с `username` → `tg_status='registered'` при импорте сразу (username — достаточный сигнал «зареган», по нему пишем напрямую через ResolveUsername; phone-resolve через checker лишний). username побеждает phone. Один choke-point `_insert_contacts_with_dedup` (push + CSV). Mig 039 — идемпотентный бэкфилл `pending`/`unchecked`+username → `registered` (терминальные `not_registered`/`error` не трогаем). Registered минует ContactCheckWorker (берёт только `pending`). 24 теста зелёные; прод-срез: 3 username-контакта, все уже registered ⇒ бэкфилл = 0 строк. NB: НЕ задеплоено (миграция применится на старте api) | 2026-06-29 | bdaabfc | [260629-kn4-username-implies-registered-status](./quick/260629-kn4-username-implies-registered-status/) |
| 260629-b7j | Фикс probe-burn чекер-пула: health-probe (`_probe_cycle`) жёг аккаунты ~4267 батчей/сутки. Теперь probe чтит `checker_rest_until` (PROBE-01), гейтится `daily_cap` (PROBE-03), троттлится ≤1 раз / `contact_check_probe_interval_seconds` (PROBE-02, деф. 15мин), деградация — эскалирующий cooldown `base*2^(trip-1)` cap 6ч + сброс на чистом recovery (PROBE-04). Миграция 036 `checker_trip_count` + 2 config-knob. §8-инварианты целы (suspect→pending, 49 контролей). 786 passed. NB: НЕ задеплоено — входит в отложенный user-gated OPS | 2026-06-29 | 13f19b4..865b880 | [260629-b7j-checker-probe-burn-fix](./quick/260629-b7j-checker-probe-burn-fix/) |
| 260629-ig7 | Переотрисовка pending-очереди при смене шаблона кампании: правка `message_template` раньше не доходила до уже стоящих в очереди элементов (очередь хранит снапшот отрендеренного текста на момент enqueue). Helper `rerender_pending_queue` (матч по identity к контакту в папке → `{{vars}}`, фолбэк на `recipient_name`; пустой шаблон = no-op; `WHERE status='pending'` пер-строчно). Авто-вызов в PATCH при реальной смене message_template + эндпоинт `POST /campaigns/{id}/rerender-pending`. 8 тестов + 44 регресс зелёные. Задеплоено (api). Фронт-кнопка — отдельно | 2026-06-29 | 37182f7 | [260629-ig7-rerender-pending-queue](./quick/260629-ig7-rerender-pending-queue/) |
| 260630-pld | Read-only доступ к `outreach_platform` для дашборда Полины через **серверный n8n** (внешний доступ/SSH/порты наружу НЕ нужны — изначальная SSH-формулировка отменена). Роль `polina_gocrazy` (LOGIN+пароль, NOSUPERUSER, `SELECT` на `public` + `ALTER DEFAULT PRIVILEGES` на будущие таблицы, `REVOKE CREATE` — без DDL/записи). Контейнер `n8n` подключён к сети `tg-outreach_default` (live `docker network connect`, 0 простоя; персистентно в `/root/apps/n8n/docker-compose.yml` по образцу `nocodb_nocodb_network`). Креды Postgres-credential (host `outreach-platform-db`/5432/`outreach_platform`/`polina_gocrazy`, SSL off) отданы в чат — **пароль НЕ в git**. Verify: SELECT ✓ · DELETE/CREATE denied ✓ · TCP-auth из сети как n8n ✓ | 2026-06-30 | _ops_ | [260630-pld-n8n-readonly-db-access-polina](./quick/260630-pld-n8n-readonly-db-access-polina/) |
| 260630-e02 | Удаление чатов из инбокса — одиночное (иконка-корзина по hover на строке) + массовое (кнопка «Select» → режим с чекбоксами, select-all, панель «Delete (N)»). Hard delete как `DELETE /conversations/{id}` (каскад messages+llm_calls из нашей БД; Telegram-диалог не трогаем). BE: новый `POST /api/v1/conversations/delete` (`{conversation_ids}`→`{deleted}`, workspace-scoped, cross-tenant ids молча скип, зеркало `contacts.delete_contacts_batch`) + схема `DeleteConversationsBatchRequest` + 2 теста (31 passed). FE: `inbox.tsx` — AlertDialog-подтверждение, invalidate `["conversations"]`+sonner-тост, паттерн зеркалит `contacts.tsx`; tsc 0 ошибок в inbox. NB: НЕ задеплоено (BE — `up -d --build api`; FE — Cloudflare). Telemetry не добавлял (вне объёма) | 2026-06-30 | BE 4084e92 · FE d3694ec | [260630-e02-delete-chats-from-ui-single-bulk-multi-s](./quick/260630-e02-delete-chats-from-ui-single-bulk-multi-s/) |
| 260629-g2z | Prompt template v2 (multi-tenant): `<core_directive>` стал универсальным, цель/раскрытие/полномочия вынесены в блоки `<objective>`/`<disclosure_policy>`/`<agent_authority>`, резолвятся из пресет-библиотек (`_OBJECTIVE_LINES`/`_DISCLOSURE_LINES`/`_AUTHORITY_LINES`) по 4 новым nullable-колонкам кампании (mig 037). NULL→дефолты (reveal_nothing/handoff_only/primary_goal) воспроизводят прежний звонковый текст. `<role>`→`<identity>`, переписан `<message_style>` (одно сообщение за ход, курир. бан-лист, before-you-send чек-лист, few-shot RU+EN с override через `style_examples`), снят противоречивый «split into two». API-проводка (Literal-энумы Create/Update/Response). 20 + 54 теста зелёные. NB: НЕ задеплоено; фронт ещё не шлёт поля | 2026-06-29 | 4022984..929c293 | [260629-g2z-prompt-template-v2](./quick/260629-g2z-prompt-template-v2/) |

### Hotfix Log — 2026-05-26 (ui-data-missing incident)

- 13:18:21 UTC: prod outreach_platform schema rebuilt by accidental `docker compose run --rm api pytest` — conftest.py::_setup_database ran `DROP SCHEMA public CASCADE` against the prod DB (`DATABASE_URL` inherited from docker-compose, no test-DB override). All operational data lost; no backups existed.
- 14:08 UTC: conftest.py guarded against non-test DSN (lines 49–77 + teardown guard at line 156). Smoke test confirms RuntimeError raised before DROP executes.
- 14:13 UTC: daily pg_dump installed at `/root/apps/aimly/tg-outreach/backup.sh`, crontab `5 3 * * *`, retention 14 days, dump path `/root/backups/tg-outreach/outreach_*.sql.gz`.
- 14:13 UTC: migrations 017-022 applied idempotently to prod (`messages` table restored, UUID `gen_random_uuid()` defaults on 13 tables, conversations.status default 'active').
- 14:14 UTC: Task 2 — race condition fix shipped. Migration 023 adds UNIQUE(user_workspaces.supabase_user_id); 3 duplicate workspaces for Andrew deleted (canonical: bb96789d-…); `_resolve_or_create_workspace` rewritten on INSERT ... ON CONFLICT DO NOTHING with orphan-Workspace cleanup. Verified by 3 parallel `[auth] resolved existing workspace=bb96789d-…` log entries.
- 14:20 UTC: follow-up — `/api/v1/conversations` returned 500 on `senders.telegram_id does not exist`. Root cause: after DROP SCHEMA, `init_db()::create_all` only rebuilt ORM columns; columns added by raw-SQL migrations 001–016 (like `senders.telegram_id` from 006) were not restored because they live in migrations only. Fix: idempotent re-apply of ALL migrations 001-023 to prod. Now `/api/v1/conversations` returns 200.

### Anti-Drift Hotfix — 2026-05-26 (follow-up to ui-data-missing)

Three structural preventatives shipped to make the schema-wipe class of incident impossible:

- 14:37 UTC: **Task A** — `log_statement=ddl` + `log_min_duration_statement=1000` set on db service via docker-compose `command:`. Successful DDL now visible in `docker logs outreach-platform-db`. Smoke verified.
- 14:42 UTC: **Task B** — `docker-compose.test.yml` overlay with `db-test` service (postgres:16 in tmpfs, ephemeral). Pytest path now `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest`. Smoke verified — conftest guard does not fire, prod file_mtime unchanged after run.
- 14:45 UTC: **Task C** — migration auto-applier in `app/database.py::_apply_migrations`. Bootstrap migration `_schema_migrations.sql` creates tracking table; on every api start, `init_db()` runs all pending `migrations/*.sql` in lexical order behind `pg_advisory_lock(7261841720260526)`. 23 existing migrations backfilled into `schema_migrations`. Fix `001` to use idempotent `DO $$ EXCEPTION duplicate_object $$` for ALTER ADD CONSTRAINT. Dockerfile now COPIES `migrations/` into image. Smoke (migration 024 round-trip) + stress test (DROP proxy_pool → restart api → applier restored it) both passed.
- CLAUDE.md updated: migrations are auto-applied (no more manual `psql -f`); pytest requires test-overlay.

### Prompt/Template Polish — 2026-05-26 (post test-campaign feedback)

- **Tone fields wired through to system prompt.** `ai_engine.get_context_for_conversation` SQL now SELECTS `voice_baseline` + `tone` JSONB. `build_system_prompt` composes `<tone>` block from voice_baseline ("Professional"/...) + tone calibration ({formal, warm, brief} 0-5) + legacy tone_of_voice TEXT. Verified by mock prompt: `<tone>Baseline persona: Professional. Tone calibration: formal=4/5, warm=2/5, brief=5/5.</tone>`.
- **Template variable `{{full_name}}` now resolves.** Added `full_name` + `fullname` as aliases to canonical `name` in `RUSSIAN_ALIASES` (despite the name). Plus smart whitespace+punctuation cleanup when a variable is empty: `"Hi {{full_name}}!"` + empty → `"Hi!"`; `"Hi {{full_name}}, how?"` + empty → `"Hi, how?"`. Sentinel-based approach avoids double-spaces and dangling punctuation. Smoke test inside container verified 5 cases.
- **POST /conversations/{id}/send accepts `message_text` (Lovable variant).** Lovable's generated client diverged from openapi.json — sends `{"message_text": "..."}` instead of canonical `{"message": "..."}`, producing 422 from Pydantic. Backend now accepts both via `validation_alias=AliasChoices("message", "message_text")`. Canonical name unchanged in spec / serialization. Smoke verified.
- **POST /conversations/{id}/send 500 → 200.** After the 422 was fixed, the next call hit `AttributeError: module 'app.services.telegram' has no attribute 'send_message_by_telegram_id'`. Root cause: `conversations.py:52` did `from app.services import telegram as telegram_service` (module rename), while every other caller uses `from app.services.telegram import telegram_service` (singleton instance). Fixed import to match the rest of the codebase.

## Session Continuity

Last session: 2026-06-30T16:24:11.891Z
Stopped at: Completed 17-01-PLAN.md
Resume file: None
