# Phase 2: TG Accounts & Contacts - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-05-21
**Phase:** 02-tg-accounts-contacts
**Areas discussed:** Модель контактов + дедуп, Sender lifecycle + rate limits, Onboarding state + listener restart, TG-проверка при импорте + checker

---

## Selected areas

| Area | Description | Selected |
|------|-------------|----------|
| Модель контактов + дедуп | contacts table shape, dedup policy, folder semantics, CSV format | ✓ |
| Sender lifecycle + rate limits | lifecycle vs auth_status, derived status, where rate limits live, green corridor handling | ✓ |
| Onboarding state + listener restart | persistence of `_onboarding_sessions`, replacement for `subprocess.run('docker restart')` | ✓ |
| TG-проверка при импорте + checker | sync vs async pipeline, missing-checker behavior, checker onboarding UX | ✓ |

---

## Модель контактов и дедуп

### Q1 — Contact model

| Option | Description | Selected |
|--------|-------------|----------|
| Новая contacts + оставить contacts_cache | Workspace-level contacts (folder_id, source, custom JSONB, tg_status); existing contacts_cache stays as per-sender Telegram resolve cache | ✓ |
| Расширить contacts_cache | Add folder_id/source/custom to contacts_cache, drop sender_id NOT NULL — mixes two roles | |
| Новая contacts + удалить contacts_cache | Lose per-sender access_hash — breaks send pipeline | |

**Notes:** Recommended choice. Two-table separation keeps semantic clarity (workspace contact list vs per-sender TG resolve cache).

### Q2 — Dedup policy

| Option | Description | Selected |
|--------|-------------|----------|
| Phone unique per workspace, skip+report | UNIQUE (workspace_id, phone), import returns summary | ✓ |
| Phone unique per folder, skip | UNIQUE (workspace_id, folder_id, phone) — flexible but conflicts in Phase 4 | |
| Phone unique per workspace, update | UPSERT on phone match — risk of clobbering names from older CSVs | |
| No unique constraint | Reputational risk: queue sends 2 messages to same person | |

**User's choice:** "давай на чистую устанавливать никакие данные из бд не тащим, это будет новый проект" — confirmed clean DB lets aggressive constraints be safe from the start. Defaults to Recommended.

### Q3 — Folder delete behavior

| Option | Description | Selected |
|--------|-------------|----------|
| Запретить если есть контакты или активная кампания | 409 Conflict with counts; `?force=true` opt-in cascade | ✓ |
| Cascade delete (с UI confirm) | ON DELETE CASCADE — easy to lose 1000 leads | |
| Move to default "Inbox" | System folder fallback — adds system-folder logic | |

**Notes:** Recommended choice. The "active campaign" half stays as a TODO-marker until Phase 4 model exists.

### Q4 — CSV format

| Option | Description | Selected |
|--------|-------------|----------|
| Фиксированный header + остальные в custom | Suggested mapping by heuristic; unknown columns → custom JSONB | |
| User-defined mapping в UI при импорте | Two-step UI flow: upload preview → user-mapped submit | ✓ |
| Строгий формат phone,name,source | Blocks CAMP-10 variables; rigid | |

**Notes:** Two-step preview + submit. Backend ships heuristic `suggested_mapping` to make UI ergonomic, but user can override.

---

## Sender lifecycle + rate limits

### Q1 — Status modeling

| Option | Description | Selected |
|--------|-------------|----------|
| Два поля + derived status в API | `lifecycle_status` (user-controlled) + `auth_status` (system) + derived `status` for UI | ✓ |
| Единый enum status с приоритетом | One column — loses paused vs error distinction | |
| Строго раздельные в API | UI duplicates priority logic — fragmentation risk | |

### Q2 — Rate limits storage + green corridor

| Option | Description | Selected |
|--------|-------------|----------|
| Per-sender + warn-only | senders.rate_per_min/hour/day with 4/20/150 defaults; soft cap warns, hard cap (10/50/300) returns 422 | ✓ |
| Per-sender + hard block above safe | Cannot exceed 4/20/150 — too rigid for power clients | |
| Per-workspace defaults + override per-sender | Two-level lookup — overkill for v1 | |

### Q3 — Warmup / paused transitions

| Option | Description | Selected |
|--------|-------------|----------|
| Явный user action + auto-error | User toggles warmup/paused in UI; error derived from auth_status | ✓ |
| Warmup auto по возрасту аккаунта | First 7 days auto-warmup — rigid heuristic | |
| Warmup = auto от listener | Listener flips warmup based on inbound messages — too magical | |

---

## Onboarding state + listener restart

### Q1 — Onboarding session persistence

| Option | Description | Selected |
|--------|-------------|----------|
| Новая таблица onboarding_sessions в БД + TTL | Persistent state; Telethon client recovered from encrypted_session_string after restart; cleanup every 5 min | ✓ |
| Sticky Telethon в памяти + persist meta в БД | Hybrid — more complex without clear win | |
| Оставить in-memory с более явным error | Defers the problem | |

### Q2 — Listener sync

| Option | Description | Selected |
|--------|-------------|----------|
| Periodic reconcile loop в listener | Listener polls senders table every 30s, diff-syncs Telethon clients | ✓ |
| Postgres LISTEN/NOTIFY | Faster but needs keepalive + fallback poll anyway | |
| Simple table flag senders.listener_dirty | Redundant alongside reconcile loop | |
| subprocess.run — оставить (deferred) | Breaks local dev and future SaaS hosting | |

---

## TG-проверка при импорте + checker

### Q1 — Check timing

| Option | Description | Selected |
|--------|-------------|----------|
| Async pipeline + tg_status='pending' | 202 Accepted; new ContactCheckWorker batches; UI polls GET /contacts | ✓ |
| Sync check в batch'ах в ходе upload | HTTP keep-alive risk for >100 contacts | |
| Async + SSE прогресс | Overkill for v1; polling is enough | |

### Q2 — Missing checker

| Option | Description | Selected |
|--------|-------------|----------|
| Skip check, tg_status='unchecked', баннер в UI | Import succeeds; UI prompts to add checker | ✓ |
| Block import, 409 'add checker first' | Bad UX for first-time clients | |
| Use one of sender accounts as fallback | Risk of banning sender via Telegram anti-spam on Resolve calls | |

### Q3 — Checker onboarding UX

| Option | Description | Selected |
|--------|-------------|----------|
| Checkbox 'this is checker account' в общем flow | One set of endpoints; role toggle on verify-code screen | ✓ |
| Отдельный flow для checker | Duplicated logic | |
| Role назначается после создания | Two-step workflow — clunky | |

---

## Claude's Discretion

- Migration filename (013_phase2.sql or split into 013/014/015)
- CSV preview storage between /preview and /import (/tmp blob vs DB row)
- Exact `warnings[]` response shape
- Endpoint and pydantic schema names (within existing project conventions)
- Wave 0 pytest fixture extensions on top of Phase 1's conftest
- Reconcile loop interval (default 30s, envvar tunable)

## Deferred Ideas

- Auto-recheck `unchecked` contacts when checker first appears → v2
- Rich proxy pool management UI (groups, rotation policies, healthchecks) → v2
- SSE/WebSocket import progress → when first 10k+ CSV client arrives
- Multi-folder per contact → v2 if real need surfaces
- `senders.is_active` cleanup in code (post-migration grep) → bundled into Plan 02-02
- `app/database.py` `Base.metadata.create_all` reconciliation (Phase 1 C-04) → planner discretion in Phase 2 or further defer
- Migrating `senders.role` from String(20)+CHECK to SQLEnum → Phase 3 with agent rewrite
- "Active campaign" half of folder-delete blocker → Phase 4 (left as TODO marker)
- Locking sender to single active campaign → Phase 4 (campaign model doesn't exist yet)
- Variable substitution `{{имя}}, {{custom.X}}` in message text → Phase 4 (at queue enqueue time)
