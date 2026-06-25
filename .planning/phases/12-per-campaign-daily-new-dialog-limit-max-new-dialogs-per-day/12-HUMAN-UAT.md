---
status: partial
phase: 12-per-campaign-daily-new-dialog-limit-max-new-dialogs-per-day
source: [12-VERIFICATION.md]
started: 2026-06-25T16:46:23Z
updated: 2026-06-25T16:46:23Z
blocked_on: coordinated backend+frontend production deploy (deferred by user)
---

## Current Test

[awaiting deploy — all 6 items require the Phase-12 backend + new frontend to be live]

## Tests

### 1. Create campaign with value 70 → warning appears, saves
expected: campaign settings form, set «новых диалогов в сутки на аккаунт» = 70 → inline warning «рекомендуем не больше 50 новых диалогов в сутки на аккаунт — выше растёт риск спам-бана» appears; save succeeds (HTTP 200) and API response carries a warnings[] entry
result: [pending]

### 2. Create campaign with value 120 → rejected
expected: value 120 → save rejected (HTTP 422, NEW_DIALOG_LIMIT_EXCEEDS_HARD_CAP)
result: [pending]

### 3. Default value (50) → no warning
expected: field defaults to 50 on a new campaign; no inline warning visible
result: [pending]

### 4. Non-default round-trip (create 70, reload → 70)
expected: create at 70, reload the campaign → field shows 70 (confirms _campaign_to_response mapping surfaces the real value, not a silent 50)
result: [pending]

### 5. PATCH re-validation (edit existing → 80)
expected: edit a campaign to 80 → warning shows; save persists; GET shows 80
result: [pending]

### 6. Live queue enforcement at cap
expected: with max_new_dialogs_per_day=2, the worker opens 2 new dialogs then stops opening NEW dialogs for that (sender,campaign) within 24h, while a follow-up to an already-contacted recipient is still sent
result: [pending]

## Summary

total: 6
passed: 0
issues: 0
pending: 6
skipped: 0
blocked: 6

## Gaps
