---
phase: 12-per-campaign-daily-new-dialog-limit-max-new-dialogs-per-day
plan: 01
subsystem: schema
tags: [migration, orm, campaigns, rate-limit, new-dialog-cap]
requires:
  - campaigns table (Phase 4, migration 016)
  - migration auto-applier (app/database.py::_apply_migrations)
provides:
  - campaigns.max_new_dialogs_per_day INT NOT NULL DEFAULT 50 (DB column)
  - Campaign.max_new_dialogs_per_day ORM column (server_default="50")
affects:
  - 12-02 (queue filter reads the column in _check_rate_limits)
  - 12-03 (API schemas expose + enforce ge=1/le=100 bounds)
tech-stack:
  added: []
  patterns:
    - "idempotent raw-SQL migration (ADD COLUMN IF NOT EXISTS, BEGIN/COMMIT)"
    - "ORM server_default duplicates DB default for create_all rebuild path"
key-files:
  created:
    - migrations/033_campaign_max_new_dialogs.sql
  modified:
    - app/models/__init__.py
decisions:
  - "D-11: DEFAULT 50 applies to ALL existing campaigns incl. running — no backfill to higher value (ADD COLUMN ... DEFAULT 50 sets every row)"
  - "D-12: no DB CHECK constraint — ge=1/le=100 bounds enforced at API layer (plan 12-03), consistent with sender rate caps"
  - "D-10: ORM server_default=\"50\" mirrors the DB default so an ORM-created Campaign with no explicit value resolves to 50"
metrics:
  duration: ~5min
  tasks: 2
  files: 2
  completed: 2026-06-25
---

# Phase 12 Plan 01: max_new_dialogs_per_day Schema Foundation Summary

Added the `campaigns.max_new_dialogs_per_day` column (INT NOT NULL DEFAULT 50) via idempotent migration 033 plus the matching `Campaign` ORM column with `server_default="50"` — the foundational schema change every other Phase-12 plan depends on (queue filter reads it, API schemas expose it).

## What Was Built

### Task 1 — Migration 033 (`migrations/033_campaign_max_new_dialogs.sql`)
- Idempotent `ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS max_new_dialogs_per_day INTEGER NOT NULL DEFAULT 50;` wrapped in `BEGIN; ... COMMIT;` (mirrors migration 032 style).
- Header comment block documents: Phase 12 NDLG-01, auto-applied via `_apply_migrations`, fail-fast, D-11 (DEFAULT 50 to all rows incl. running, no backfill), D-12 (no DB CHECK, API enforces bounds).
- No `UPDATE`/backfill statement — `ADD COLUMN ... DEFAULT 50` already sets every existing row (incl. running campaigns) to 50, which is exactly D-11.
- No CHECK constraint / no ALTER TYPE — the ge=1/le=100 bounds live at the API layer (D-12, plan 12-03).
- Commit: `b0a3087`

### Task 2 — Campaign ORM column (`app/models/__init__.py`)
- Added `max_new_dialogs_per_day = Column(Integer, nullable=False, server_default="50")` inside the `Campaign` class (line 567), placed right after `recontact_min_age_days` among the per-campaign policy fields.
- `server_default="50"` duplicates the migration-033 DB default — required by the CLAUDE.md rule that the `create_all` rebuild path (post-DROP-incident) reconstructs tables from the ORM, not from migrations.
- `Column`/`Integer` already imported; no other column touched; file still parses (`ast.parse` exit 0).
- Commit: `dc2f55e`

## Deviations from Plan

None — plan executed exactly as written.

## Verification

- `grep` for the exact DDL line, `BEGIN;`, `COMMIT;` — all present (verify=OK).
- `ADD COLUMN IF NOT EXISTS max_new_dialogs_per_day` appears once; no `UPDATE campaigns`; no `ALTER TYPE`/`CHECK (`.
- ORM column line is between `class Campaign(Base):` (503) and `class CampaignSender` (584); count=1; `ast.parse` exit 0.
- Smoke (docker rebuild) NOT run — executor discretion in a worktree; the fail-fast auto-applier would block api startup if 033 were broken, validated on deploy.

## Self-Check: PASSED

- FOUND: migrations/033_campaign_max_new_dialogs.sql
- FOUND: app/models/__init__.py (max_new_dialogs_per_day column present)
- FOUND commit b0a3087 (Task 1)
- FOUND commit dc2f55e (Task 2)
