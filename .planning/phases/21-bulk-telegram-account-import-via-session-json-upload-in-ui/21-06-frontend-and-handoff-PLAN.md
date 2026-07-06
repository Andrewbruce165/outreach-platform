---
phase: 21-bulk-telegram-account-import-via-session-json-upload-in-ui
plan: 06
type: execute
wave: 5
depends_on: ["21-05"]
files_modified:
  - lovable-handoff/openapi.json
  - lovable-handoff/types/api.ts
autonomous: false
requirements: [IMPT-09]
must_haves:
  truths:
    - "openapi.json + generated types expose the 3 new import endpoints (preview, confirm, status) so Lovable can generate a typed client"
    - "The UI lets a user upload one ZIP, see the recognized set (matched/unpaired/malformed), pick a role, confirm, and watch per-account progress"
    - "A mixed batch (valid pair, orphan .json, orphan .session, duplicate) renders correct per-account result rows"
  artifacts:
    - path: "lovable-handoff/openapi.json"
      provides: "regenerated spec including /accounts/import/* endpoints"
      contains: "/import/preview"
  key_links:
    - from: "sibling repo import UI"
      to: "/api/v1/accounts/import/{job_id}/status"
      via: "poll while job status != done"
      pattern: "import status poll"
---

<objective>
Expose the Phase 21 API to the Lovable frontend and build the two-step bulk-import UI: regenerate the openapi.json handoff + types from the running backend, then implement (in the sibling repo `aimly-tg-outreach`) the ZIP upload -> preview summary -> role radio -> confirm -> progress-poll flow, and validate it with a human against a real mixed batch.

Purpose: The backend (21-01..05) is Lovable-consumable only once the spec is regenerated; the UI is where the client actually performs a bulk import. This is the cross-repo, human-verify closer for the phase.
Output: regenerated `lovable-handoff/openapi.json` + `types/api.ts`; sibling-repo import UI; human UAT sign-off.
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/phases/21-bulk-telegram-account-import-via-session-json-upload-in-ui/21-CONTEXT.md
@.planning/phases/21-bulk-telegram-account-import-via-session-json-upload-in-ui/21-RESEARCH.md

<interfaces>
Handoff regen is a scripted, idempotent flow — DO NOT hand-edit openapi.json:
  scripts/export-handoff.sh   (rebuilds `docker compose up -d db api`, pulls /openapi.json from inside the
  api container, runs `npx -y openapi-typescript@7` for types/api.ts, verifies info.title). MUST rebuild the
  api container FIRST so the spec is not stale (prior phases hit stale-spec).

Backend endpoints to surface (from 21-03 / 21-05):
  POST /api/v1/accounts/import/preview            (multipart ZIP) returns import_id + matched/unpaired/malformed
  POST /api/v1/accounts/import/{import_id}/confirm  body role sender|checker, returns 202 job_id + total
  GET  /api/v1/accounts/import/{job_id}/status     returns job_id,status,total,processed,items[basename,status,result,reason]

Sibling repo (INDEPENDENT git remote AGS-Venture-Lab/aimly-tg-outreach): /root/apps/aimly/aimly-tg-outreach
  React + TanStack Start + TypeScript + shadcn; routes under src/routes, components under src/components.
  Commits inside that folder go to the sibling remote — NOT to outreach-platform. Do not `git add -A`.
  Prior account UI (Phase 20) lives in the accounts page — add the bulk-import entry there.
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Regenerate openapi.json + types from the running backend</name>
  <read_first>
    - scripts/export-handoff.sh (the regen flow + the info.title sanity check)
    - app/routers/account_import.py (the 3 endpoints + their Pydantic response models — the source of the spec)
  </read_first>
  <files>lovable-handoff/openapi.json, lovable-handoff/types/api.ts</files>
  <action>
    Rebuild the api container so the spec includes the Phase 21 router, then run the handoff export:
    `cd /root/apps/aimly/tg-outreach && docker compose up -d --build api && bash scripts/export-handoff.sh`.
    Confirm `lovable-handoff/openapi.json` now contains the three import paths and that `lovable-handoff/types/api.ts` regenerated with the new operation types. Do NOT hand-edit the spec — if a path/model is missing, fix the backend router/schema and re-run. Stage ONLY `lovable-handoff/openapi.json` + `lovable-handoff/types/api.ts` (and any `design-source` files the script rewrites) — do not sweep unrelated changes.
  </action>
  <verify>
    <automated>grep -q "accounts/import/preview" /root/apps/aimly/tg-outreach/lovable-handoff/openapi.json && grep -q "job_id}/status" /root/apps/aimly/tg-outreach/lovable-handoff/openapi.json && grep -q "import_id}/confirm" /root/apps/aimly/tg-outreach/lovable-handoff/openapi.json && echo OK</automated>
  </verify>
  <acceptance_criteria>
    - openapi.json contains the three `/api/v1/accounts/import/*` paths (preview, confirm, status)
    - `lovable-handoff/types/api.ts` was regenerated (mtime newer than before; contains the new operations)
    - No manual edits to openapi.json (produced solely by export-handoff.sh)
    - The verify command prints OK
  </acceptance_criteria>
  <done>openapi.json + types regenerated from the rebuilt backend and expose all three import endpoints; no hand-editing.</done>
</task>

<task type="auto">
  <name>Task 2: Sibling-repo two-step bulk-import UI (ZIP -> preview -> role -> confirm -> poll)</name>
  <read_first>
    - /root/apps/aimly/aimly-tg-outreach/src/routes (the accounts page + routing pattern)
    - /root/apps/aimly/tg-outreach/lovable-handoff/types/api.ts (the regenerated types for the 3 endpoints)
    - /root/apps/aimly/tg-outreach/lovable-handoff/error-codes.md (structured error-code display convention)
  </read_first>
  <files>aimly-tg-outreach (sibling repo): src/routes accounts page + new import component(s)</files>
  <action>
    In the sibling repo `/root/apps/aimly/aimly-tg-outreach`, add a bulk-import surface reachable from the accounts page (e.g. an "Импорт аккаунтов" button opening a modal/route). Implement the two-step flow using the regenerated typed client:
    - Step 1 (upload): a single file input accepting one `.zip`; POST it as multipart to `/api/v1/accounts/import/preview`; on success render the recognized set — matched pairs (basename, phone, has-2FA / has-proxy flags), unpaired files, malformed files (+reason). Show counts prominently.
    - Role selection: a radio `sender` | `checker` (default `sender`) — one choice for the whole batch (D-16). Disable Confirm until a role is chosen.
    - Step 2 (confirm): POST `/import/{import_id}/confirm` with `{ role }`; on 202 store the returned `job_id` and switch to a progress view.
    - Progress: poll `GET /import/{job_id}/status` (e.g. every ~2s) until `status === 'done'`; render a per-account result table (basename + a status chip: ok / already_connected / failed + reason). Stop polling on done.
    - Handle the structured 4xx codes (FILE_TOO_LARGE, ZIP_TOO_LARGE, TOO_MANY_ACCOUNTS, BAD_ZIP, IMPORT_EXPIRED, INVALID_ROLE) with human-readable messages via the existing error-code display convention.
    Commit inside the sibling repo only; stage specific files (no `git add -A`). Run the sibling's typecheck (`tsc`) clean before handing to UAT.
  </action>
  <verify>
    <automated>cd /root/apps/aimly/aimly-tg-outreach && (npx tsc --noEmit 2>&1 | tail -5) && echo TSC_DONE</automated>
  </verify>
  <acceptance_criteria>
    - A bulk-import entry exists on the accounts page (grep the accounts route/component for an import trigger)
    - The flow calls all three endpoints: preview (multipart), confirm (with role), status (polled)
    - A role radio (sender/checker) gates Confirm
    - The progress view polls status until done and renders per-account result rows
    - `tsc --noEmit` is clean (TSC_DONE printed with no type errors above it)
  </acceptance_criteria>
  <done>Sibling-repo UI implements upload -> preview -> role radio -> confirm -> progress-poll with per-account result rows and structured-error display; typecheck clean.</done>
</task>

<task type="checkpoint:human-verify" gate="blocking">
  <name>Task 3: Human UAT — real mixed-batch bulk import</name>
  <action>Human-only verification of the deployed end-to-end flow (backend already automated by Tasks 1-2 and plans 21-01..05). The executor prepares a mixed-batch test ZIP from the gitignored scratchpad sample and presents the how-to-verify steps below; no further code is written unless UAT reports an issue.</action>
  <what-built>
    Backend import pipeline (preview/confirm/worker/status) + sibling-repo two-step bulk-import UI + regenerated openapi/types.
  </what-built>
  <how-to-verify>
    1. Build a test ZIP from the gitignored vendor sample in `scratchpad/` (+18646884306.json + .session) plus a deliberately-mixed set: one valid pair, one orphan `.json` (no matching `.session`), one orphan `.session`, and a duplicate of an already-connected account.
    2. In the deployed UI (https://aimly.agsventurelab.com, accounts page), open Import accounts, upload the ZIP.
    3. Confirm the preview shows: 1+ matched, the orphans as unpaired, and any malformed JSON listed.
    4. Pick role = checker (or sender), confirm; watch the progress view.
    5. Verify per-account rows resolve to: valid pair -> ok; orphans -> not attempted; duplicate -> already_connected; and any auth-failed -> failed + reason. The batch completes (status=done) even with the broken entries.
    6. Confirm an imported account appears in the accounts list as active, and (manual reconnect check) that triggering a real connect for it (e.g. resync) succeeds with no forced re-login / security prompt and `sender_restriction_events` stays clean (validates the per-account fingerprint, IMPT-04 manual-only item).
    7. Confirm no plaintext 2FA appears anywhere in the UI or network responses.
  </how-to-verify>
  <resume-signal>Type "approved" or describe issues (per-account row wrong, batch aborted on a broken entry, re-login prompt on reconnect, 2FA leak).</resume-signal>
</task>

</tasks>

<verification>
- openapi.json/types expose the 3 import endpoints; no hand-edits.
- UI drives the full two-step flow and polls to done; per-account rows correct on a mixed batch.
- Imported account reconnects without a security-flag (manual — validates fingerprint).
- No 2FA plaintext in any UI/network surface.
</verification>

<success_criteria>
- IMPT-09 delivered: Lovable-consumable spec + working two-step bulk-import UI, human-verified end-to-end on a real mixed batch.
</success_criteria>

<output>
After completion, create `.planning/phases/21-bulk-telegram-account-import-via-session-json-upload-in-ui/21-06-SUMMARY.md`
</output>
