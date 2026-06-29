---
quick_id: 260629-ig7
slug: rerender-pending-queue
title: Re-render pending queue items when campaign message_template changes
date: 2026-06-29
status: complete
commits: 37182f7 (+ docs)
---

# Quick Task 260629-ig7 — SUMMARY

## What shipped

Closed the footgun where editing a running campaign's `message_template` left
already-`pending` `message_queue` rows carrying the old rendered opener (the queue
snapshots rendered text at enqueue — [[project-queue-snapshots-template-at-enqueue]]).
Now a template edit propagates to pending rows automatically (PATCH) and on demand
(endpoint), sharing one helper.

## Changes

- **`app/services/campaign_enqueue.py`** — `rerender_pending_queue(db, campaign) -> int`:
  re-renders `message_text` of all `pending` / `item_type='message'` rows for the
  campaign with the current template, matching each row to its contact by identity
  (`recipient_phone == COALESCE(phone,'@'||username)`) in the campaign folder so
  `{{vars}}` render with the same data; falls back to the stored `recipient_name`
  when the contact is gone. Empty template → no-op (never blanks). `UPDATE … WHERE
  status='pending'` re-checks per row (skips in-flight). Does NOT commit.
- **`app/routers/campaigns.py`**:
  - `patch_campaign` auto-calls the helper when `message_template` actually changes
    (compared to the stored value, before setattr), atomic with the change; logs count.
  - `POST /campaigns/{id}/rerender-pending` → `{rerendered: int}`, workspace-scoped.

## Commits

- `37182f7` — feature (helper + endpoint + PATCH hook)
- (next) — tests (8) ; docs (PLAN + SUMMARY)

## Verification

- `tests/test_rerender_pending_queue.py`: 8 passed (helper: pending vs sent, vars,
  fallback, empty no-op; router: PATCH hook, no-op PATCH, endpoint, cross-workspace 404).
- Regression: `test_campaign_router` + `test_campaign_enqueue_worker` +
  `test_phase5_1_campaign_v2_router` → 44 passed.
- test-overlay only.

## Deploy

- api rebuilt (`docker compose up -d --build api`) — no migration in this task.
- No listener change.

## Follow-ups

- Frontend can add a "refresh queue" button → `POST /campaigns/{id}/rerender-pending`
  (separate repo).
- `style_examples` / preset fields from the earlier prompt-v2 task still need UI wiring.
