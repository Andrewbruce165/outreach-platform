---
phase: 22-account-level-new-chat-limit-grades
plan: 07
subsystem: api-contract-and-frontend-handoff
tags: [openapi, handoff, grade, lovable, contract]
status: checkpoint-pending
requires: ["22-02", "22-04", "22-06"]
provides:
  - regenerated openapi.json + types/api.ts reflecting grade endpoints/fields
  - frontend handoff note for sibling aimly-tg-outreach repo
affects:
  - lovable-handoff/openapi.json
  - lovable-handoff/types/api.ts
  - sibling repo /root/apps/aimly/aimly-tg-outreach (downstream UI work)
tech-stack:
  added: []
  patterns:
    - "openapi.json regenerated from running FastAPI via scripts/export-handoff.sh — never hand-edited"
key-files:
  created:
    - .planning/phases/22-account-level-new-chat-limit-grades/22-FRONTEND-HANDOFF.md
  modified:
    - lovable-handoff/openapi.json
    - lovable-handoff/types/api.ts
decisions:
  - "Rebuilt the shared prod api from the main checkout (not the worktree) then ran export-handoff.sh there, copying the two regenerated artifacts into the worktree to commit — avoids worktree/volume path conflicts while regenerating against the live app (D-12)"
metrics:
  tasks_completed: 2
  tasks_total: 3
  files_created: 1
  files_modified: 2
  completed: 2026-07-08
---

# Phase 22 Plan 07: Publish Grade Contract + Frontend Handoff Summary

Regenerated the Lovable handoff contract from the live prod api so it exposes the account-grade endpoints/fields and drops the retired throttle fields, and published a contract-linked frontend handoff note for the sibling repo. **Task 3 (human-verify checkpoint) is pending — plan is NOT complete.**

## What Was Built

- **Task 1 (D-12) — regenerated openapi.json + types/api.ts** (commit `efc7679`). Rebuilt the prod api container (`docker compose up -d --build api`) from the main checkout so the merged Wave 1–3 grade code went live, then ran `scripts/export-handoff.sh` (exit 0, UI-SPEC drift check passed: 39/39 endpoints present). The regenerated contract now carries `GET/PUT /api/v1/sender-grade-settings`, `PATCH /api/v1/senders/{slug}/grade`, and the extended `SenderResponse` (`current_level`, `level_updated_at`, `remaining_daily_budget`); it no longer references `max_new_dialogs_per_day` or the sender `rate_per_day`/`per_day` rate field (the only remaining `per_day` are the new grade-ladder `levelN_chats_per_day` fields).
- **Task 2 (D-11) — frontend handoff note** (commit `3eab457`). Created `22-FRONTEND-HANDOFF.md` describing the three UI deliverables (workspace ladder editor, per-card grade + remaining budget, manual override) each mapped to the regenerated contract with documented request/response shapes (including the untyped GET/PUT response bodies read from `app/routers/grade_settings.py`), the removals to purge (`max_new_dialogs_per_day`, sender `per_day` rate, and the "TODAY x/150" column whose `/150` denominator is gone), and an explicit note that no UI-SPEC.md exists — this note + openapi.json are the contract.

## Verification Results

**Task 1 acceptance (all pass):**
- `sender-grade-settings` in openapi.json: 1
- `/senders/{slug}/grade`: 2
- `current_level`: 5, `remaining_daily_budget`: 1, `level_updated_at`: 3
- `max_new_dialogs_per_day`: 0 (want 0)
- `rate_per_day`: 0 (want 0)

**Task 2 acceptance (all pass):**
- 22-FRONTEND-HANDOFF.md exists, references `sender-grade-settings` / `/grade` / `current_level` (5 hits)
- lists campaign `max_new_dialogs_per_day` + sender `per_day` removals
- notes no UI-SPEC.md exists (2 mentions)

## Deviations from Plan

**1. [Rule 3 — Blocking issue] Prod api container predated the grade code**
- **Found during:** Task 1
- **Issue:** The running prod api (built ~3h earlier) did not expose the grade endpoints — regenerating against it would have produced a stale contract. Additionally, running `docker compose --build api` from the isolated worktree risks container-name/volume conflicts with the prod stack.
- **Fix:** Rebuilt the api from the main checkout (`/root/apps/aimly/tg-outreach`, HEAD `f959656` = merged Waves 1–3), confirmed the live api exposed the grade paths, ran `export-handoff.sh` there, then copied the two regenerated artifacts into the worktree and committed them on the worktree branch.
- **Files modified:** lovable-handoff/openapi.json, lovable-handoff/types/api.ts
- **Commit:** efc7679

Also note: the worktree branch spawned from a stale base (phase 19). Merged current `main` into the worktree branch before starting so grade code + dropped columns were present (fast-forward, no conflicts).

## CHECKPOINT PENDING — Task 3 (human-verify, blocking)

Task 3 is a cross-repo human-verify checkpoint (VALIDATION marks the UI behaviors manual-only). It was NOT resolved by the executor — control returned to the orchestrator/user for approval. The plan must not be marked complete until approved. See the returned checkpoint block for the exact verification steps.

## Self-Check: PASSED
- FOUND: lovable-handoff/openapi.json
- FOUND: lovable-handoff/types/api.ts
- FOUND: .planning/phases/22-account-level-new-chat-limit-grades/22-FRONTEND-HANDOFF.md
- FOUND commit: efc7679
- FOUND commit: 3eab457
