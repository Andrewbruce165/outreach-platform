---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: executing
stopped_at: Phase 05.1 UI-SPEC approved
last_updated: "2026-05-23T09:15:30.850Z"
last_activity: 2026-05-23
progress:
  total_phases: 8
  completed_phases: 7
  total_plans: 27
  completed_plans: 27
  percent: 100
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-05-21)

**Core value:** Клиент подключил аккаунт и через 10 минут первая кампания запущена — без программистов, без DevOps, без настройки серверов.
**Current focus:** Phase 05.1 — lovable-ui-v1

## Current Position

Phase: 6
Plan: Not started
Status: Executing Phase 05.1
Last activity: 2026-05-23

Progress: [██░░░░░░░░] 17% (1/6 phases done)

## Performance Metrics

**Velocity:**

- Total plans completed: 9
- Average duration: —
- Total execution time: —

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 01 | 3 | - | - |
| 05.1 | 6 | - | - |

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

### Roadmap Evolution

- Phase 05.1 inserted after Phase 5: Lovable UI v1 — auth + onboarding + TG accounts + contacts + agents + campaigns + inbox + analytics + settings (URGENT — closes Core Value + 7 HUMAN-UAT items from Phase 5)

### Pending Todos

None yet.

### Blockers/Concerns

- Phase 4 первым планом — аудит существующего webhook + function calling (вынести с уровня sender/AIContext на уровень кампании)
- rotation.py:59,89,122,138 still references DROPPED context_contact_assignments table — 04-04 must rewrite per AUDIT TODO #6 (context_id → campaign_id signature)

## Session Continuity

Last session: 2026-05-22T18:40:08.798Z
Stopped at: Phase 05.1 UI-SPEC approved
Resume file: .planning/phases/05.1-lovable-ui-v1/05.1-UI-SPEC.md
