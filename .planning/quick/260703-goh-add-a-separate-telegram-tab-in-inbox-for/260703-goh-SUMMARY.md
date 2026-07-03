---
task: 260703-goh
title: Separate "Telegram" tab in inbox for service-account (login/auth-code) messages
type: quick
subsystem: inbox / listener
tags: [inbox, listener, telegram-service, conversations, status]
requirements: [TGTAB-01, TGTAB-02]
key-files:
  created:
    - migrations/046_telegram_service_status.sql
  modified:
    - app/schemas/__init__.py
    - app/services/listener.py
    - app/routers/conversations.py
    - tests/test_phase5_inbox.py
    - tests/conftest.py
    - lovable-handoff/openapi.json
decisions:
  - "Telegram service messages (id 777000 / +42777) are now PERSISTED (not dropped) under a new conversation status='telegram_service'"
  - "The new status is hidden from the default inbox list and surfaced only via ?status=telegram_service (mirrors bot_ignored)"
  - "ai_enabled=false always; the handler never calls the AI engine, never enqueues, never touches sender restriction/lifecycle"
metrics:
  duration: ~4min
  tasks: 2
  files: 7
  completed: 2026-07-03
---

# Quick 260703-goh: Separate "Telegram" Inbox Tab Summary

Telegram login/auth-code notifications from the official service account (id `777000` / phone `+42777`) are now persisted to `conversations` + `messages` under a distinct `status='telegram_service'` instead of being silently dropped by the listener. They are hidden from the normal inbox and reachable via `GET /api/v1/conversations?status=telegram_service`, giving the frontend everything it needs to render a dedicated "Telegram" tab — without touching the sibling frontend repo.

## What changed

**Task 1 — Persist & classify (commit `21f8ef6`)**
- `migrations/046_telegram_service_status.sql` — idempotent (`DROP CONSTRAINT IF EXISTS` + `duplicate_object` guard) extension of `conversations_status_check` to add `'telegram_service'`, preserving all existing values (`no_reply`, `bot_ignored`, etc.). Auto-applied on api startup (verified live: `[migrate] OK 046_telegram_service_status`; live constraint now lists `telegram_service`).
- `app/schemas/__init__.py` — added `"telegram_service"` to `CONVERSATION_STATUSES` so `PATCH /conversations/{id}` validation accepts it.
- `app/services/listener.py` — the early-`return` drop at the `777000/+42777` branch is replaced by a delegation to a new `_handle_telegram_service_message(...)`. The new handler is modeled on `_handle_bot_message`: isolated `AsyncSessionLocal` + try/except, SELECT-then-INSERT/UPDATE conversation (`ai_enabled=false`, `status='telegram_service'`, `paused_at=NOW()`, `paused_reason='Telegram service account (login/auth codes)'`), Pitfall-3 guard (only downgrades from `active`), and an inbound message INSERT with `ON CONFLICT (conversation_id, telegram_message_id) DO NOTHING`. It never calls the AI engine, never enqueues, and never touches sender restriction/lifecycle.

**Task 2 — Expose via API + tests + openapi (commit `e2d81ac`)**
- `app/routers/conversations.py` — default-hide extended to `c.status NOT IN ('bot_ignored', 'telegram_service')` when `status` is unset; explicit `?status=telegram_service` returns exactly those rows. Docstring updated.
- `tests/test_phase5_inbox.py` — added `test_list_hides_telegram_service_by_default` and `test_list_status_telegram_service_explicit`.
- `tests/conftest.py` — added migration 046 to the hardcoded test-DB migration list (Rule 3 fix; the list is not a glob so a new migration must be registered or the test DB rejects the new status).
- `lovable-handoff/openapi.json` — regenerated OFFLINE via `app.openapi()` in the test container (no prod un-gate). Since `status` is free-form `str`, the only diff is the endpoint docstring — expected and correct.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Register migration 046 in the test-DB conftest**
- **Found during:** Task 2 (the two new tests failed with `CheckViolationError` on `conversations_status_check`).
- **Issue:** `tests/conftest._setup_database` applies migrations from a hardcoded list, not a glob, so the test DB's CHECK constraint never gained `telegram_service` and rejected the seeded rows.
- **Fix:** Added an exists-guarded `_mig_046` apply block immediately after 045 (same pattern used for 044/045).
- **Files modified:** `tests/conftest.py`
- **Commit:** `e2d81ac`

## Frontend handoff

The frontend repo (`/root/apps/aimly/aimly-tg-outreach`) is a separate Lovable repo and OUT OF SCOPE for this task. To build the "Telegram" tab, the frontend team should:

- **Add a new inbox tab labeled "Telegram".**
- **List:** the tab lists conversations from `GET /api/v1/conversations?status=telegram_service` (workspace-scoped, auth/JWT required). The exact status string is `telegram_service`. These rows are hidden from the default (`status`-less) inbox list, so no double-counting.
- **Messages:** open a conversation and load its messages exactly as any other tab via `GET /api/v1/conversations/{id}/messages`.
- **Semantics:** these conversations are AI-disabled by design (`ai_enabled=false`). They are login/auth-code notifications from Telegram's official service account (`id=777000` / `+42777`), not real contacts — the AI answerer never fires on them. `contact_name` is typically "Telegram"; `paused_reason='Telegram service account (login/auth codes)'`.
- **Contract source:** `lovable-handoff/openapi.json` (regenerated by this task). `status` is a free-form string on both the query param and `ConversationResponse`, so no enum was added — the tab is a status-filter, matching the existing `bot_ignored` pattern.

## Verification

- `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_phase5_inbox.py tests/test_listener.py -q` → 34 passed.
- Migration 046 auto-applied on api startup (`[migrate] OK 046_telegram_service_status`); live `conversations_status_check` includes `telegram_service`.
- `api` + `listener` containers rebuilt and running.
- `grep` confirms `telegram_service` wired across migration 046, schemas, listener, and conversations router.

## Self-Check: PASSED
- FOUND: migrations/046_telegram_service_status.sql
- FOUND: commit 21f8ef6 (Task 1)
- FOUND: commit e2d81ac (Task 2)
- Live DB constraint includes `telegram_service`; both containers Up.
