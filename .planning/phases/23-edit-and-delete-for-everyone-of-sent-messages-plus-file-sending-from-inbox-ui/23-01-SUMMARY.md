---
phase: 23-edit-and-delete-for-everyone-of-sent-messages-plus-file-sending-from-inbox-ui
plan: 01
subsystem: database
tags: [postgres, migration, pydantic, pytest, telethon, inbox, media]

# Dependency graph
requires:
  - phase: 05-inbox-analytics
    provides: "messages table (raw-SQL, mig 017), conversations router, MessageResponse, GET /messages"
provides:
  - "migration 053: messages.message_type (NOT NULL DEFAULT 'text' + CHECK) + file_name/mime_type/size_bytes + edited_at; message_text NOT NULL relaxed"
  - "conftest exists-guarded apply of migration 053 (hardcoded list does NOT glob)"
  - "MessageResponse extended (message_text Optional + 5 media/edit fields)"
  - "EditMessageRequest (alias-tolerant) + SendFileFromUIResponse schemas"
  - "Wave-0 RED scaffold tests/test_phase23_inbox_mutations.py (22 tests, INBM-01..08)"
affects: [23-02 endpoints/service methods, 23-03 GET messages SELECT widen, 23-04 listener save_message media, 23-05 download endpoint]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Import-inside-body / endpoint-call RED scaffold so the file collects with 0 errors while Wave-2 endpoints/methods do not exist"
    - "xfail(strict=False) marker for not-yet-implemented behavioural clusters; schema cluster left as a genuine GREEN gate"
    - "New TelegramService methods patched with raising=False so the monkeypatch itself does not error before the method lands"

key-files:
  created:
    - migrations/053_phase23_messages_media.sql
    - tests/test_phase23_inbox_mutations.py
    - .planning/phases/23-.../23-01-SUMMARY.md
  modified:
    - tests/conftest.py
    - app/schemas/__init__.py

key-decisions:
  - "message_type value set locked to text|photo|video|voice|document (no generic 'file'); voice branch maps to 'voice'"
  - "Hard-delete per D-03 — NO deleted_at column"
  - "messages stays raw-SQL (NO Message ORM model) — DB DEFAULT is the sole source of message_type default (avoids the mig 040/042 ORM default drift)"
  - "New media/edit fields on MessageResponse all optional/defaulted so the current GET /messages SELECT still constructs the model until 23-03 widens it"

patterns-established:
  - "Wave-0 RED scaffold with a single GREEN schema gate + xfailed behavioural clusters"

requirements-completed: [INBM-08]

# Metrics
duration: 12min
completed: 2026-07-08
---

# Phase 23 Plan 01: Schema Migration + RED Scaffold Summary

**Migration 053 extends the raw-SQL `messages` table with `message_type`+CHECK, media metadata (file_name/mime_type/size_bytes), an `edited_at` marker and a relaxed `message_text` NOT NULL; conftest applies it, `MessageResponse` grows the media/edit fields, and a 22-test Wave-0 RED scaffold covers all six INBM clusters.**

## Performance

- **Duration:** ~12 min
- **Started:** 2026-07-08T08:19:01Z
- **Completed:** 2026-07-08T08:31:00Z
- **Tasks:** 3
- **Files modified:** 4 (2 created, 2 modified) + this SUMMARY

## Accomplishments
- Idempotent migration 053: `message_type VARCHAR(20) NOT NULL DEFAULT 'text'` + duplicate_object-guarded CHECK (text|photo|video|voice|document), nullable `file_name`/`mime_type`/`size_bytes`, nullable `edited_at`, and `message_text DROP NOT NULL` (file bubbles).
- conftest applies migration 053 via an exists-guarded `_mig_053` block (mirrors the 045/046 pattern) — required because the hardcoded migration list does NOT glob and `messages` has no ORM model.
- `MessageResponse.message_text` made Optional and 5 fields added (message_type/file_name/mime_type/size_bytes/edited_at), all optional/defaulted; new `EditMessageRequest` (alias-tolerant) + `SendFileFromUIResponse`.
- `tests/test_phase23_inbox_mutations.py`: 22 tests across schema / GET-messages / save_message / edit / delete / send-file / incoming-media / download. Schema cluster GREEN (validates mig 053); the rest xfail until Waves 2-5 implement them.

## Task Commits

1. **Task 1: Migration 053 — extend messages table (idempotent)** - `0ca24a7` (feat)
2. **Task 2: conftest 053-apply + Wave-0 RED test scaffold** - `df3c8d6` (test)
3. **Task 3: Extend MessageResponse + EditMessageRequest / SendFileFromUIResponse** - `241574b` (feat)

## Files Created/Modified
- `migrations/053_phase23_messages_media.sql` - messages media/edit DDL (idempotent, no deleted_at, no ORM model)
- `tests/conftest.py` - exists-guarded `_mig_053` apply
- `app/schemas/__init__.py` - MessageResponse extended + EditMessageRequest + SendFileFromUIResponse
- `tests/test_phase23_inbox_mutations.py` - Wave-0 RED scaffold (22 tests)

## Verification
- `pytest tests/test_phase23_inbox_mutations.py -k schema -x` → 2 passed (mig 053 columns present, NULL text accepted, default 'text' applied, CHECK rejects unknown type).
- `--collect-only` → 22 collected, 0 errors.
- Schema import smoke → `ok` (MessageResponse constructs with message_text omitted).
- Regression: full phase-23 file + `test_phase5_inbox_send_takeover.py` → 8 passed, 14 xfailed, 6 xpassed, 0 failed (MessageResponse change did not break the existing send path).

## Decisions Made
- Followed the plan as specified (message_type set, hard-delete/no deleted_at, no ORM model, optional/defaulted response fields).

## Deviations from Plan

None to the plan's own task instructions. Two environmental/reconciliation notes below (handled without changing plan intent).

### Notes for reconciliation (not code deviations)

**1. Migration 055 already bridges the same 4 media columns on `main`.**
- **Context:** Phase 24 (already merged on `main`) shipped `migrations/055_messages_media_columns.sql` adding `message_type`/`file_name`/`mime_type`/`size_bytes` + the same CHECK, because Phase 23 had not yet executed. Migration 053 is fully idempotent (`ADD COLUMN IF NOT EXISTS` + duplicate_object guard + `DROP NOT NULL` no-op), so on `main` it no-ops those four columns and adds what 055 did NOT: `edited_at` and the `message_text` NOT NULL relaxation. Lexical order (053 before 055) is safe — both are idempotent. No conflict; no action needed beyond keeping both files.

**2. Executed in a stale worktree base (Phase 19) → cherry-pick model.**
- **Context:** This executor's worktree branched from commit `92bd54b` (Phase 19), so migrations only went to 045 and there was no phase-23 planning dir. Work was done faithfully against the worktree content: the conftest `_mig_053` block was anchored after `_mig_045` (the last present block) instead of after `_mig_046` (absent here) — functionally identical since all exists-guarded blocks are idempotent and order-independent. Commits are intended to be cherry-picked onto `main`; each is self-contained (new migration file, new test file, additive conftest/schema edits) and cherry-picks cleanly.
- **STATE.md / ROADMAP.md / REQUIREMENTS.md were intentionally NOT committed from this worktree** — the worktree copies are frozen at Phase 19 while `main` is at Phase 24; committing them would clobber `main`'s newer state on cherry-pick. The orchestrator must apply these updates on `main`: advance-plan (Phase 23 Plan 1→2), update-progress, record-metric, `roadmap update-plan-progress 23`, and `requirements mark-complete INBM-08`.

## Issues Encountered
- Docker network address pool exhausted (many leftover `agent-*_default` networks; host-wide `docker network prune` denied on the shared server). Worked around by reusing the existing `tg-outreach_default` network (`-p tg-outreach`) and pointing the ephemeral test run at the main checkout's `.env` (`--env-file /root/apps/aimly/tg-outreach/.env`) — the worktree has no `.env`. Test overlay still targets the ephemeral `db-test` (outreach_test), so no prod DB was touched.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Schema + response fields + RED scaffold are in place; Wave-2 (23-02) can add the edit/delete/send-file endpoints + the four TelegramService methods and flip those xfails to green. 23-03 widens `GET /messages` SELECT; 23-04 extends listener `save_message`; 23-05 adds the download endpoint.
- Blocker for orchestrator: apply STATE/ROADMAP/REQUIREMENTS updates on `main` (see reconciliation note 2) — not committed from the stale worktree.

## Self-Check: PASSED

- Files: migrations/053_phase23_messages_media.sql, tests/test_phase23_inbox_mutations.py, 23-01-SUMMARY.md all FOUND.
- Commits: 0ca24a7, df3c8d6, 241574b all FOUND.
- conftest `053_phase23_messages_media.sql`, `EditMessageRequest`, `SendFileFromUIResponse` all FOUND.

---
*Phase: 23-edit-and-delete-for-everyone-of-sent-messages-plus-file-sending-from-inbox-ui*
*Completed: 2026-07-08*
