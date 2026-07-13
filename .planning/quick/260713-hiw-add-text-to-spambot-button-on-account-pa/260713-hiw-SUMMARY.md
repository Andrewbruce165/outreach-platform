---
phase: quick-260713-hiw
plan: 01
status: complete
subsystem: telegram-outreach / account-management
tags: [spambot, conversations, listener, inbox, frontend, sheet]
requires:
  - conversations table (raw-SQL) + status CHECK (migrations 045/046/061)
  - listener antispam branches (ANTISPAM_BOT_IDS incl. 178220800)
  - POST /conversations/{id}/send (reused as-is)
  - _load_sender_by_slug workspace-scoped auth
provides:
  - conversations.status value 'spambot'
  - POST /api/v1/senders/{slug}/spambot-conversation (get-or-create)
  - listener._persist_spambot_message (additive @SpamBot persistence)
  - frontend SpambotChatPanel + 'Написать SpamBot' account-card button
affects:
  - GET /conversations default list (now excludes 'spambot')
  - POST /conversations/{id}/send auto-takeover (no longer clobbers 'spambot')
tech-stack:
  added: []
  patterns:
    - "dedicated conversation status to keep a system chat out of the Inbox (mirrors 'telegram_service')"
    - "additive listener persistence before an unchanged safety-net handler"
    - "shadcn Sheet side-panel reusing the polling/send endpoints of the Inbox Thread"
key-files:
  created:
    - migrations/062_conversations_status_spambot.sql
    - tests/test_spambot_conversation.py
    - frontend/src/components/SpambotChatPanel.tsx
  modified:
    - app/services/listener.py
    - app/routers/senders.py
    - app/routers/conversations.py
    - tests/conftest.py
    - frontend/src/routes/_authenticated/accounts.tsx
decisions:
  - "status='spambot' (dedicated) so the @SpamBot chat is excluded from the Inbox by default"
  - "sentinel contact_phone='spambot:178220800' (NOT NULL column) matches no real recipient → send-path queue-cancel is a harmless no-op"
  - "persist BEFORE _handle_antispam_signal, runs for ALL @SpamBot msgs incl. 'free' — restriction parsing left byte-for-byte unchanged"
  - "kept migration filename 062 per plan must_have despite a concurrent 062 collision (see Deviations)"
metrics:
  duration: ~40min
  tasks: 2
  files: 8
  completed: 2026-07-13
---

# Quick Task 260713-hiw: Text to SpamBot — per-sender @SpamBot live chat panel

Managers can now open a right-side Sheet from an account card and chat 1:1 with
Telegram's official @SpamBot (id 178220800) from that specific sender account —
sending/receiving through that account's session — without the SpamBot chat ever
polluting the normal Inbox, and with the existing automatic restriction-parsing
safety net left completely untouched.

## What was built

### Task 1 — Backend (commit 916dfa2)
- **migration 062** — `conversations_status_check` re-added with the full current
  legal set (incl. `lead_pending`/`telegram_service`) plus `'spambot'`; idempotent
  DROP-IF-EXISTS + ADD wrapped in the standard `DO $$ … duplicate_object $$` block.
  Matching conftest block added so the test DB exercises the constraint change.
- **`listener._persist_spambot_message`** — mirrors `_handle_telegram_service_message`:
  isolated `AsyncSessionLocal`, get-or-create a `status='spambot'` conversation
  (`ai_enabled=false`, sentinel `contact_phone='spambot:178220800'`,
  `contact_name='@SpamBot'`, `contact_telegram_id=178220800`), then insert the inbound
  message with `sent_by='contact'` + `ON CONFLICT … DO NOTHING`. Never touches
  restriction/lifecycle status; try/except only logs.
- Wired into **both** antispam branches (bot-flag ~813 and keyword-backup ~837): when
  `sender.id == 178220800`, persist BEFORE the unchanged `_handle_antispam_signal`.
- **`POST /api/v1/senders/{slug}/spambot-conversation`** — workspace-scoped get-or-create
  via `_load_sender_by_slug`; returns `{conversation_id, status:'spambot'}`.
- **conversations.py guards** — `'spambot'` added to the Inbox-list exclusion; send
  auto-takeover UPDATE gained `AND status <> 'spambot'` so it never flips to `'manual'`.
- **tests** — get-or-create idempotency, ai_enabled=false + hidden-from-Inbox,
  cross-tenant 404.

### Task 2 — Frontend (commit 957ca55)
- **`SpambotChatPanel.tsx`** — shadcn `Sheet` (side=right). On open, POSTs the
  get-or-create endpoint via `useQuery`; once the id is known, polls
  `GET /conversations/{id}/messages` every 10s; composer sends via the reused
  `POST /conversations/{id}/send`, clears + invalidates on success, `toast.error` on
  `ApiError`. Outbound right / inbound left bubbles.
- **`accounts.tsx`** — `'Написать SpamBot'` button (MessageSquare icon) in the
  SenderCard actions menu right after "Check Spam Bot"; renders the panel alongside
  the existing modals.

## Verification
- Backend targeted suite: `tests/test_spambot_conversation.py` + `tests/test_spambot_selfcheck.py`
  → **10 passed** via the test-overlay.
- Frontend: `tsc --noEmit` — new/changed files (`SpambotChatPanel.tsx`, `accounts.tsx`)
  compile with **zero errors**. (See Deviations for pre-existing unrelated errors.)

## Deviations from Plan

### Environment: concurrent agent + migration-number collision (IMPORTANT — needs human reconciliation)
A **second agent was actively editing this same checkout** throughout execution
(a "proxy-switch-listener-lag" feature). It touched `app/config.py`,
`app/models/__init__.py`, `app/services/queue.py`, `app/services/warmup.py`,
`app/services/contact_check_worker.py`, `app/routers/senders.py`,
`app/services/listener.py`, several test files, and created a **colliding**
`migrations/062_sender_proxy_switch_pending.sql`.

Handling (per the parallel-agent memory rule — never `git add -A`, stage only own files):
- For the two SHARED files (`senders.py`, `listener.py`) I rebuilt a **my-only**
  version from `HEAD` (re-applying only my edits) and staged it as a git blob via
  `hash-object` + `update-index`, **without touching the working tree** — so the
  concurrent agent's uncommitted edits to those files are fully preserved. Verified:
  post-commit `git diff` shows only their proxy-switch hunks remaining, uncommitted.
- **Migration 062 collision:** two files now share the `062_` prefix. The auto-applier
  tracks by full filename in `schema_migrations` and applies in lexical order, so both
  apply independently — **no functional break**. I kept my filename `062_…` because the
  plan's must_have artifact mandates that exact path. **A human/orchestrator should
  renumber one of the two 062 migrations to 063 to restore the unique-number convention**
  once the proxy-switch feature is committed.

### Pre-existing out-of-scope tsc errors (not fixed)
`tsc --noEmit` reports 3 pre-existing `/login` route `search`-param errors in
`__root.tsx`, `_authenticated.tsx`, `settings.tsx` — files this task did not touch
(confirmed unchanged from baseline). Logged to `deferred-items.md`; left as-is per the
scope boundary.

## Known Stubs
None — both directions (send + persisted @SpamBot replies) are wired to real endpoints.

## Self-Check: PASSED
- All 8 created/modified files present on disk.
- Both commits present: 916dfa2 (backend), 957ca55 (frontend).
