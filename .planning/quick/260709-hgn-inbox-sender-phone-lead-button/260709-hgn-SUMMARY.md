---
phase: quick-260709-hgn
plan: 01
status: complete
completed: 2026-07-10
commits:
  - 20d58f1  # backend mark-lead endpoint + tests
  - e0fabc3  # frontend sender-phone label + Lead button
  - 51f4ca6  # backend finish endpoint + tests (follow-on)
  - 5c37c17  # frontend Finish button (follow-on)
---

# Quick Task 260709-hgn — Inbox sender-phone + Lead button — SUMMARY

## What was delivered

Two Inbox UI improvements plus the backend endpoint the second one needed.

1. **Sender-filter dropdown label** — now shows `name (phone)` instead of `name (@slug)`,
   falling back to `@slug` when the sender has no phone. `<option value>` stays `s.id`
   (only the visible label changed). `frontend/src/routes/_authenticated/inbox.tsx`.

2. **"Lead" button** in the conversation detail header — one-click manual lead marking.
   - New backend endpoint `POST /api/v1/conversations/{id}/mark-lead`
     (`app/routers/conversations.py`), workspace-scoped, mirrors the automatic
     auto-lead flow in `ai_engine._handle_builtin_signal(mark_as_lead)`: sets
     `status='lead'` (**`ai_enabled` untouched** — lead is a marker, the conversation
     continues) and fires the campaign lead webhook via `notify_signal(event_type='lead',
     reason='Marked as lead manually via UI', …)` fire-and-forget after commit.
     The existing `PATCH /{id}` only sets status and does **not** fire the webhook, so
     downstream n8n consumers now see the same `lead` event the AI signal produces.
   - Frontend `markLeadMut` mutation + button (Flag icon), disabled + green active-styled
     when `status === 'lead'`, invalidates `["conversation"]` / `["conversations"]` on success.

## Decisions (locked with user before planning)

- Dropdown label = **name + phone** (not phone-only).
- Lead button = **status + webhook** (not status-only) — must behave like the AI's
  `mark_as_lead` signal for downstream integrations.

## Verification

- Backend: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api
  pytest tests/test_phase5_inbox_manager_mode.py -k mark_lead` → **2 passed**
  (status='lead' + ai_enabled unchanged; cross-workspace 404).
- Frontend: `bun run build` (Docker `oven/bun:1`) → **built clean**, prerender OK.

## Notes / deviations

- No migration added — `status='lead'` is already in the `conversations.status` CHECK
  constraint (`app/models/__init__.py:358`).
- The original worktree-isolated executor was cut off mid-run by an org spend limit after
  writing the backend files (tests GREEN) but before committing or doing the frontend.
  Work was completed on the main tree directly; the abandoned worktree/branch were removed.
- `contacts` has no `contact_id` FK on `conversations`; the endpoint LEFT JOINs contacts on
  `(workspace_id, phone)` to populate the webhook payload.

## Follow-on (2026-07-10): manual Finish button

Same pattern extended per user request — a **"Finish"** button beside Lead.
- New endpoint `POST /api/v1/conversations/{id}/finish` mirrors
  `ai_engine._handle_builtin_signal(finish_conversation)`: sets
  `status='finished'`, **`ai_enabled=false`** + `paused_at`/`paused_reason='Finished
  manually via UI'` (finishing ENDS the conversation, so the AI is turned off —
  unlike lead, which leaves AI running), then fires the campaign **finish** webhook.
- Frontend `finishMut` + button (CheckCheck icon), muted-grey active state,
  disabled when `status==='finished'`.
- Tests: `-k "mark_lead or finish"` → **5 passed**; frontend build clean.
- **Deployed** 2026-07-10 (api rebuild + `./deploy-frontend.sh`); `/finish` route
  live (POST→401 auth-gated).

## Deployment

**All changes deployed to prod** 2026-07-10:
- Backend: `docker compose up -d --build api` (clean start, routes registered).
- Frontend: `./deploy-frontend.sh` (published to `/var/www/aimly`).
