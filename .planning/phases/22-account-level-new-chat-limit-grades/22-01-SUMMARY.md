---
phase: 22-account-level-new-chat-limit-grades
plan: 01
subsystem: schema-foundation
tags: [migrations, orm, grade-ladder, warmup-registry]
requires: []
provides:
  - senders.current_level / senders.level_updated_at (grade storage)
  - sender_first_contacts (new-warmup-pair registry)
  - sender_grade_settings (per-workspace 3-level ladder)
  - app/services/grade_ladder.py (shared resolver)
affects:
  - 22-02 (settings API reads sender_grade_settings / load_ladder)
  - 22-03 (queue rewrite reads current_level + budget_for_level)
  - 22-04 (sender API surfaces current_level)
  - 22-05 (warmup budget reads sender_first_contacts + ladder)
  - 22-06 (drops rate_per_day / max_new_dialogs_per_day after readers rewritten)
tech-stack:
  added: []
  patterns:
    - idempotent raw-SQL migrations (IF NOT EXISTS / DO$$ duplicate_object / ON CONFLICT DO NOTHING)
    - server_default mandatory on every NOT NULL ORM column (create_all/raw-INSERT drift guard)
    - conftest exists-guarded per-migration SQL-only blocks (no glob)
key-files:
  created:
    - migrations/056_sender_grade_columns.sql
    - migrations/057_sender_first_contacts.sql
    - migrations/058_sender_grade_settings.sql
    - app/services/grade_ladder.py
    - tests/test_grade_foundation.py
  modified:
    - app/models/__init__.py
    - tests/conftest.py
decisions:
  - "D-14: current_level INT 1..3 + level_updated_at TIMESTAMPTZ on senders; grade timer backfilled to created_at (D-10)"
  - "D-08: sender_first_contacts canonical LEAST/GREATEST pair PK, idempotently backfilled from warmup_sessions + warmup_messages so already-warmed pairs are not re-charged"
  - "D-16: sender_grade_settings fixed 3-level ladder; absent row = code-defaults (5/30, 9/30, 13); migration seeds nothing"
  - "D-17: level 3 is permanent — no level3_step_days; grade_ladder step is None for level 3"
  - "rate_per_day and max_new_dialogs_per_day left in place — removal deferred to 22-06 after readers rewritten (RESEARCH Pitfall 6)"
metrics:
  duration: ~8min
  completed: 2026-07-08
status: complete
---

# Phase 22 Plan 01: Grade-Ladder Schema Foundation Summary

Additive data-model foundation for account-level new-chat grade limits: grade storage columns on `senders`, the `sender_first_contacts` new-warmup-pair registry, the per-workspace `sender_grade_settings` ladder table, and the shared `grade_ladder.py` resolver that every Wave-2 feature plan imports. Zero behavior change — nothing reads these columns until Wave 2.

## What Was Built

**Task 1 — migration 056 + ORM grade columns (218e121)**
- `migrations/056_sender_grade_columns.sql`: idempotent `ADD COLUMN IF NOT EXISTS current_level INT NOT NULL DEFAULT 1` + `level_updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`; guarded `senders_current_level_range` CHECK (1..3); backfill `level_updated_at = created_at` so the grade timer starts at account creation (D-10/D-14), re-run-safe.
- `app/models/__init__.py` Sender: `current_level` + `level_updated_at` mirrored with `server_default` (create_all builds fresh/test schema from ORM; raw INSERTs omit these — project-orm-default-vs-server-default-drift). `rate_per_day` left intact.

**Task 2 — migrations 057/058 + ORM models + resolver (0ad7d74)**
- `migrations/057_sender_first_contacts.sql`: `sender_first_contacts` with canonical `sender_a_id < sender_b_id` composite PK; two idempotent backfills (from `warmup_sessions` and `warmup_messages`, both `LEAST/GREATEST`-canonicalised, `ON CONFLICT DO NOTHING`) so already-warmed pairs are recorded once and never re-charged as new (D-08).
- `migrations/058_sender_grade_settings.sql`: per-workspace fixed 3-level ladder (`level{1,2,3}_chats_per_day` + `level{1,2}_step_days`), defaults 5/30, 9/30, 13; seeds nothing (absent row = code-defaults, D-16); no level-3 step (D-17).
- `app/models/__init__.py`: `SenderFirstContact` + `SenderGradeSettings` ORM models; every NOT NULL column carries `server_default`.
- `app/services/grade_ladder.py`: `LADDER_DEFAULTS = [(5, 30), (9, 30), (13, None)]`; `resolve_ladder(row)`, `budget_for_level(ladder, level)`, `step_days_for_level(ladder, level)`, `async load_ladder(db, workspace_id)` (bind params only, missing row → defaults).

**Task 3 — conftest SQL-only blocks + foundation test (5fd101a)**
- `tests/conftest.py`: exists-guarded `_mig_056` (SQL-only backfill + CHECK) and `_mig_057` (warmup-pair backfill) blocks, hardcoded filenames (conftest does NOT glob — RESEARCH Pitfall 1). No block for 058 (pure CREATE TABLE built by create_all).
- `tests/test_grade_foundation.py`: (1) grade columns exist + fresh sender defaults `current_level=1`; (2) 057 backfill idempotent — already-warmed pair recorded exactly once; (3) `load_ladder` returns 5/9/13 budgets for a workspace with no settings row.

## Verification

- `pytest tests/test_grade_foundation.py -x` via test-overlay: **3 passed in 2.50s**.
- `grep -rn "max_new_dialogs_per_day\|rate_per_day" app/models/__init__.py` still shows both (removals correctly deferred to 22-06).

Test-overlay note: this plan runs inside a git worktree, which uses a distinct compose project name and collides with the fixed `container_name: outreach-platform-db`. Ran with `COMPOSE_PROJECT_NAME=tg-outreach --env-file <prod .env> ... run --rm --no-deps api pytest`, reusing the already-healthy `db-test` (targets the ephemeral `outreach_test` DB — prod DB never touched).

## Deviations from Plan

None — plan executed exactly as written. No Rule 1-3 auto-fixes were required.

Adjustment worth recording (not a plan deviation): the worktree branched from a stale ancestor (`92bd54b`, pre-dating migrations 046-055 and the phase-22 directory). Fast-forwarded the worktree branch to `main` before starting so the plan's `read_first` anchors (mig 052, conftest `_mig_053/054/055` blocks) existed and my commits stack cleanly on current `main` — avoiding the known worktree-stale-base spurious-deletion hazard.

## Known Stubs

None.

## Self-Check: PASSED
- migrations/056_sender_grade_columns.sql — FOUND
- migrations/057_sender_first_contacts.sql — FOUND
- migrations/058_sender_grade_settings.sql — FOUND
- app/services/grade_ladder.py — FOUND
- tests/test_grade_foundation.py — FOUND
- commit 218e121 — FOUND
- commit 0ad7d74 — FOUND
- commit 5fd101a — FOUND
