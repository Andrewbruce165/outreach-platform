---
status: complete
quick_id: 260619-bdm
date: 2026-06-19
---

# Quick Task 260619-bdm: Bulk delete + move contacts (folders)

## Goal
Let a user delete contacts from a folder and move contacts between folders — from the UI.

## What existed already
Backend already had: single move (`POST /contacts/{id}/move`), batch move (`POST /contacts/move`),
single delete (`DELETE /contacts/{id}`). Frontend had decorative-only checkboxes and a
`toast.info("Move to… coming soon")` stub. Gap = bulk delete endpoint + the whole UI wiring.

## Changes

### Backend (`outreach-platform`, branch `feat/contacts-bulk-delete-move`)
- `app/schemas/__init__.py`: `DeleteContactBatchRequest` (`contact_ids`, min_length=1).
- `app/routers/contacts.py`: `POST /api/v1/contacts/delete` → `{deleted: N}`, workspace-scoped,
  cross-tenant ids silently skipped (mirror of batch move). — commit `ddceca9`
- `tests/test_contacts.py`: 3 tests (own delete, cross-tenant skip, empty→422).
- `lovable-handoff/openapi.json`: regenerated (new path + schema only).
- **Drift fix** `migrations/027_folders_workspace_name_unique.sql` + `tests/conftest.py`:
  the `folders(workspace_id, name)` UNIQUE constraint declared inline in 013 never landed
  (create_all pre-creates the table, so `CREATE TABLE IF NOT EXISTS` is a no-op; 019 missed
  folders). Verified absent in prod. `get_or_create_by_name`'s ON CONFLICT would crash at
  runtime. conftest was also stuck at migrations 018+026 — added 019–027, fixing ~85
  previously-red tests. — commit `88aa13c`
- `app/routers/folders.py`: include `active_campaigns: []` in `FOLDER_NOT_EMPTY` 409 to match
  the existing (pre-existing-red) test contract. — commit `f5153ae`

### Frontend (`aimly-tg-outreach`, branch `feat/contacts-bulk-delete-move`, commit `85eadb5`)
- `src/routes/_authenticated/contacts.tsx`: working row + select-all checkboxes (selection
  resets on folder change), selection toolbar with **Move to…** (folder dropdown →
  `POST /contacts/move`), **Delete** (confirm → `POST /contacts/delete`), **Clear**.
  Invalidates contacts + folders queries.

## Verification
- Backend: `test_contacts.py` (22) + `test_folders.py` (10) green via test-overlay.
  Full suite went 148→63 failures (−85) from the conftest migration fix; 0 regressions.
- Frontend: `tsc --noEmit` exit 0. Remaining eslint errors are pre-existing prettier
  violations in untouched Lovable code.

## Deploy notes (not yet deployed)
- Both repos are on feature branches, **not merged**. User controls deploy.
- On backend deploy, the auto-applier runs migration 027 (adds folders UNIQUE — prod has 0 dupes, safe).
- Backend rebuild: `docker compose up -d --build api`. Frontend: Cloudflare/wrangler.
- Pre-existing uncommitted changes to `app/services/ai_engine.py` / `tests/test_ai_engine.py`
  were left untouched.
