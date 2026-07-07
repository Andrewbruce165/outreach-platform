---
phase: 21-bulk-telegram-account-import-via-session-json-upload-in-ui
plan: 06
subsystem: ui
tags: [lovable-handoff, openapi, openapi-typescript, tanstack-start, shadcn, react-query, account-import, cross-repo, multipart-upload, status-poll]

# Dependency graph
requires:
  - phase: 21-03-preview-unzip-pair-stage
    provides: "POST /accounts/import/preview (multipart ZIP → import_id + matched/unpaired/malformed) — the Pydantic response models that shape the regenerated spec"
  - phase: 21-05-async-job-confirm-worker-status
    provides: "POST /import/{import_id}/confirm (202 job_id) + GET /import/{job_id}/status (processed/total + per-item report) + AccountImportWorker"
provides:
  - "lovable-handoff/openapi.json + types/api.ts regenerated from the rebuilt backend — the 3 import operations (preview/confirm/status) are now Lovable-consumable typed contracts"
  - "sibling repo aimly-tg-outreach: AccountImportModal — two-step bulk-import UI (upload one ZIP → recognized-set preview → sender|checker role radio → confirm → 2s status poll → per-account result chips) reachable from the accounts page 'Import accounts' button"
  - "sibling error-codes.ts: structured import 4xx codes (FILE_TOO_LARGE, ZIP_TOO_LARGE, TOO_MANY_ACCOUNTS, BAD_ZIP, IMPORT_EXPIRED, INVALID_ROLE, IMPORT_NOT_FOUND, JOB_NOT_FOUND) → friendly UI strings"
affects: []

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Scripted, idempotent handoff regen (rebuild api FIRST so the spec is not stale → export-handoff.sh → openapi.json + openapi-typescript types) — never hand-edit the spec"
    - "Cross-repo type sync: the sibling's src/types/api.ts is a straight copy of the regenerated lovable-handoff/types/api.ts (purely-additive: 3 import operations + a backend-truth refresh of daily_cap corridor / has_backup required)"
    - "Two-step async import UI: multipart preview → recognized-set + role radio → 202 confirm → react-query refetchInterval poll that returns false once status==='done' (mirrors the KB-ingest doc-poll pattern)"
    - "Secrets-free by construction on the client: the UI only ever renders basenames + has_2fa/has_proxy flags + status/result/reason — never the twoFA value nor session bytes (D-07)"

key-files:
  created:
    - "aimly-tg-outreach (sibling): src/components/AccountImportModal.tsx"
  modified:
    - lovable-handoff/openapi.json
    - lovable-handoff/types/api.ts
    - "aimly-tg-outreach (sibling): src/routes/_authenticated/accounts.tsx"
    - "aimly-tg-outreach (sibling): src/lib/error-codes.ts"
    - "aimly-tg-outreach (sibling): src/types/api.ts"

key-decisions:
  - "Copied the WHOLE regenerated lovable-handoff/types/api.ts into the sibling's src/types/api.ts rather than surgically merging — the diff is purely additive (the 3 import operations + a backend-truth refresh of daily_cap corridor 10/30 and has_backup→required); the sibling only READS health.has_backup so nothing breaks (tsc clean)"
  - "Duplicate-account already_connected demonstrated within a single UAT batch by shipping the same session bytes under a distinct display basename (edited session_file) — the worker dedups by telegram_id post-connect, so the 2nd copy resolves already_connected without depending on prior workspace state"
  - "Per-account result chip maps the worker's item.status (pending/processing/ok/failed) + result-code together: ok+already_connected → 'Already connected' (ghost pill), ok+imported → 'Imported' (green), failed → result-code label + reason (red)"

patterns-established:
  - "The phase-closing cross-repo human-verify plan: regen handoff (backend commit) + build sibling UI (sibling commit) + real mixed-batch UAT against the deployed stack"

requirements-completed: [IMPT-09]

# Metrics
duration: 45min
completed: 2026-07-07
---

# Phase 21 Plan 06: Frontend & Handoff Summary

**Regenerated the Lovable openapi.json + types from the rebuilt backend (3 import operations now typed) and shipped the sibling-repo two-step bulk-import UI — upload one ZIP → recognized-set preview → sender/checker role radio → 202 confirm → 2s status poll → per-account result chips — human-verified end-to-end on a real mixed batch (2 matched incl. a dedup, 2 unpaired, 1 malformed) plus a live 13/13 real-archive import.**

## Performance

- **Duration:** 45 min (spans the blocking human-verify checkpoint)
- **Started:** 2026-07-07T09:02:29Z
- **Completed:** 2026-07-07T09:47:45Z
- **Tasks:** 3 (2 auto + 1 checkpoint:human-verify)
- **Files modified:** 6 (1 created, 5 modified — across 2 repos)

## Accomplishments

- **Task 1 (backend handoff):** rebuilt the api container (migration 051 applied cleanly, `AccountImportWorker` came up — 8 workers total), then ran `scripts/export-handoff.sh` to regenerate `lovable-handoff/openapi.json` + `types/api.ts`. All three import paths (`/import/preview`, `/import/{import_id}/confirm`, `/import/{job_id}/status`) with their operations are now in the spec; produced solely by the script (no hand-edits). Verify command printed `OK`.
- **Task 2 (sibling UI):** created `AccountImportModal.tsx` and wired an "Import accounts" button into the accounts page. Two-step flow using the regenerated typed client: multipart preview → matched pairs (phone + 2FA/proxy flags) / unpaired / malformed(+reason) with prominent counts → sender|checker role radio (gates Confirm, D-16) → `POST confirm` → progress view polling status every 2s until `done` → per-account result chips. Structured 4xx codes surfaced via the existing error-code convention. Synced the sibling's `src/types/api.ts`. `npx tsc --noEmit` clean.
- **Task 3 (human UAT):** built + validated a deliberately-mixed test ZIP; human verified the deployed flow end-to-end. Approved.

## Task Commits

1. **Task 1: Regenerate openapi.json + types** — `c72ea45` (chore, **backend repo** `tg-outreach`)
2. **Task 2: Sibling two-step bulk-import UI** — `486834d` (feat, **sibling repo** `aimly-tg-outreach`) → rebased by the coordinator onto 9 newer Lovable commits as **`5c15a9b`**, tsc clean, pushed to `AGS-Venture-Lab/aimly-tg-outreach`, Cloudflare-deployed (that deploy is what the human tested)
3. **Task 3: Human UAT** — no commit (checkpoint:human-verify)

**Plan metadata:** _(this commit)_ (docs: complete plan — backend repo, `.planning/` only)

## Files Created/Modified

- `lovable-handoff/openapi.json` — regenerated spec incl. the 3 `/api/v1/accounts/import/*` operations (backend repo).
- `lovable-handoff/types/api.ts` — regenerated openapi-typescript types (backend repo).
- `aimly-tg-outreach: src/components/AccountImportModal.tsx` — the two-step import modal (upload → preview + role → confirm → poll → per-account results).
- `aimly-tg-outreach: src/routes/_authenticated/accounts.tsx` — "Import accounts" topbar button + modal render + `importOpen` state.
- `aimly-tg-outreach: src/lib/error-codes.ts` — 8 import 4xx codes → friendly strings.
- `aimly-tg-outreach: src/types/api.ts` — synced from the regenerated handoff types (purely additive).

## Decisions Made

- **Whole-file type sync (not surgical merge):** the diff between the sibling's stale `src/types/api.ts` and the regenerated handoff types was purely additive — the 3 import operations plus a backend-truth refresh (`daily_cap` corridor tightened to 10/30, `has_backup` now required). The sibling only reads `health.has_backup`, so the required-flip is safe; tsc is clean. Copying the whole file is exactly "use the regenerated typed client."
- **already_connected demonstrated in-batch:** the UAT ZIP ships the same session bytes twice under distinct display basenames (edited `session_file`); the worker's post-connect telegram_id dedup makes the second copy resolve `already_connected` regardless of prior workspace state.
- **Result-chip mapping:** the chip reads `item.status` + `item.result` together (ok+already_connected → ghost "Already connected", ok+imported → green "Imported", failed → result-code label + red + reason).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Sibling `src/types/api.ts` had to be synced from the regenerated handoff types**
- **Found during:** Task 2 (the sibling's own type copy lacked the 3 import operations, so `components["schemas"]["AccountImportPreviewResponse"]` etc. would not exist and tsc/import would fail)
- **Issue:** The plan's `files_modified` lists only the backend handoff files; but the sibling consumes its OWN `src/types/api.ts`, which was stale (no import operations). Without the sync the typed client couldn't reference the new schemas.
- **Fix:** Copied the freshly regenerated `lovable-handoff/types/api.ts` → sibling `src/types/api.ts` (verified the diff is purely additive: 3 import ops + backend-truth `daily_cap`/`has_backup` refresh; 0 removals that affect sibling usage).
- **Files modified:** aimly-tg-outreach: src/types/api.ts
- **Verification:** `grep -c` for the 3 import schemas → present; `npx tsc --noEmit` clean.
- **Committed in:** `486834d` → `5c15a9b` (Task 2 sibling commit)

---

**Total deviations:** 1 auto-fixed (1 blocking).
**Impact on plan:** Necessary for the UI to reference the new endpoints via the typed client. No product-scope creep — the sibling type sync is the standard cross-repo handoff step (same as Phase 08-04 / 10-04 / 11-04 / 20-05).

## Issues Encountered

- **Cross-repo commit rebase (coordinator-handled):** the local sibling commit `486834d` was based on session HEAD; the coordinator rebased it onto 9 newer Lovable commits → `5c15a9b`, re-ran tsc (clean), pushed to `AGS-Venture-Lab/aimly-tg-outreach`, and Cloudflare deployed. This is the known worktree/parallel-Lovable reconcile pattern (memory `feedback-worktree-executor-stale-base-cherry-pick`); no Lovable commit was dropped.
- **DB collation-version warning** (`collation version 2.41 vs OS 2.36`) appears on every prod `psql` — a pre-existing libc/env mismatch, unrelated to this plan, out of scope.

## Known Stubs

None. The UI drives real endpoints against real staged/imported data; the preview/status payloads are secrets-free by construction (backend, D-07). No placeholder/mock data paths.

## Authentication Gates

None — the api rebuild + handoff regen and the sibling build/typecheck required no external credentials. The frontend push to `AGS-Venture-Lab/aimly-tg-outreach` + Cloudflare deploy was performed by the coordinator.

## User Setup Required

None — no external service configuration required.

## Live UAT Evidence (human-verified, IMPT-09 + IMPT-04)

- **Mixed-batch preview** rendered exactly as designed: **2 Matched** (`+18646884306`, `+18646884306-dup`), **2 Unpaired** (`+15551230001.json`, `+15551230002.session`), **1 Malformed** (`+15551230003.json` — invalid JSON). Screenshot confirmed.
- **Real archive:** 13/13 accounts imported ok (job `da5998a0` → `done`, all items ok/imported, role sender). Duplicates → `already_connected`. Broken entries did NOT abort the batch (IMPT-07).
- **Live backend (prod):** all 13 new senders `active`, `restriction_status='none'`, `client_fingerprint` set, proxy assigned; the listener auto-reconnected to them with their own fingerprints (`catch_up` + "слушаем сообщения" in logs), **ZERO** auth/security errors, **ZERO** `sender_restriction_events` — confirming **IMPT-04 reconnect-without-re-login live**.
- **No plaintext 2FA** anywhere in UI/network (these vendor JSONs carried no 2FA; the API returns flags only, D-07).

## Next Phase Readiness

- **IMPT-09 delivered:** Lovable-consumable spec + working two-step bulk-import UI, human-verified end-to-end on a real mixed batch and a live 13/13 real import.
- **Phase 21 execution complete** (6/6 plans). All 10 IMPT requirements now closed. Ready for `/gsd:verify-work 21`.

---
*Phase: 21-bulk-telegram-account-import-via-session-json-upload-in-ui*
*Completed: 2026-07-07*

## Self-Check: PASSED

- SUMMARY + created component exist; backend commit `c72ea45` present in `tg-outreach` git log; sibling commit `486834d` → `5c15a9b` pushed to the sibling remote.
- Task 1 verify printed `OK` (3 import paths in openapi.json); Task 2 `npx tsc --noEmit` clean.
- Task 3 human UAT approved with live prod evidence (13/13 import, IMPT-04 reconnect verified, zero restriction events).
