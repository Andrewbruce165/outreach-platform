---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: executing
stopped_at: Completed 04-03-PLAN.md (per-campaign scheduling — queue.py rewrite + 21 tests)
last_updated: "2026-05-22T08:42:08.569Z"
last_activity: 2026-05-22
progress:
  total_phases: 7
  completed_phases: 4
  total_plans: 18
  completed_plans: 16
  percent: 17
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-05-21)

**Core value:** Клиент подключил аккаунт и через 10 минут первая кампания запущена — без программистов, без DevOps, без настройки серверов.
**Current focus:** Phase 04 — campaigns

## Current Position

Phase: 04 (campaigns) — EXECUTING
Plan: 4 of 5
Status: Ready to execute
Last activity: 2026-05-22

Progress: [██░░░░░░░░] 17% (1/6 phases done)

## Performance Metrics

**Velocity:**

- Total plans completed: 3
- Average duration: —
- Total execution time: —

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 01 | 3 | - | - |

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

### Pending Todos

None yet.

### Blockers/Concerns

- Phase 4 первым планом — аудит существующего webhook + function calling (вынести с уровня sender/AIContext на уровень кампании)
- rotation.py:59,89,122,138 still references DROPPED context_contact_assignments table — 04-04 must rewrite per AUDIT TODO #6 (context_id → campaign_id signature)

## Session Continuity

Last session: 2026-05-22T08:42:08.564Z
Stopped at: Completed 04-03-PLAN.md (per-campaign scheduling — queue.py rewrite + 21 tests)
Resume file: None
