# 12-04 SUMMARY — OpenAPI handoff regen + frontend campaign field

**Status:** Code complete. **Production deploy + human-UAT DEFERRED** by explicit user decision (coordinated backend+frontend release later).

## What was built

### Task 1 — OpenAPI handoff regeneration (backend repo, NDLG-05, D-15)
- Regenerated `lovable-handoff/openapi.json` + `lovable-handoff/types/api.ts` to include `max_new_dialogs_per_day` (×3: Create/Update/Response) and the `CampaignWriteResponse {campaign, warnings[]}` wrapper (×4).
- **Deviation from plan (approved by user):** the plan's Task 1 said rebuild the prod api container first. Because that rebuild would deploy ALL Phase-12 backend to the live server — including the **breaking** `CampaignWriteResponse` shape change to campaign create/patch — the user chose "frontend + spec, no deploy". The spec was instead dumped from a **throwaway container** (`docker compose run --rm --no-deps -v $PWD/app:/app/app api python -c "…app.openapi()…"`) with the current source mounted, no DB, **leaving the running prod api untouched** (verified Up, not recreated).
- Sanity: `jq .info.title` = "Outreach Platform API"; `git diff --stat lovable-handoff/` = only openapi.json + types/api.ts.
- Commit: `5961169` (backend repo).

### Task 2 — Frontend campaign field (sibling repo, NDLG-06, D-16)
- Sibling repo `/root/apps/aimly/aimly-tg-outreach` (independent; commits → AGS-Venture-Lab/aimly-tg-outreach).
- Synced regenerated `types/api.ts` + `types-openapi.json` (same mechanism as prior phase commit `9aea0ef`).
- Added numeric `max_new_dialogs_per_day` input (default 50, min 1, max 100) to the campaign builder wizard (`campaigns.new.tsx`, ScheduleStep) and the edit form (`EditCampaignModal.tsx`), beside work-hours / re-contact fields. Label "Новых диалогов в сутки на аккаунт" + per-account help text.
- Inline warning shown only when value > 50, exact copy: «рекомендуем не больше 50 новых диалогов в сутки на аккаунт — выше растёт риск спам-бана» (role="alert").
- Wired into create/PATCH payloads. Handled the breaking response shape: create/patch call sites typed as `CampaignWriteResponse` and unwrap `res.campaign`; GET/list/lifecycle/duplicate paths (flat `CampaignResponse`) left untouched.
- `tsc --noEmit` exit 0; `vite build` exit 0.
- Commit: `cfb2a51` (sibling repo) — **NOT pushed** (ahead 1).

### Task 3 — Human-verify checkpoint (blocking)
**PENDING — requires the deployed frontend, which is deferred.** Cannot run UAT until backend + frontend are deployed.

## Deferred (awaiting user's go-ahead for a coordinated release)
1. Deploy Phase-12 backend to prod: `cd /root/apps/aimly/tg-outreach && docker compose up -d --build api` (applies migration 033 to prod DB, activates the per-account daily cap + the **breaking** CampaignWriteResponse on the live API).
2. Push + deploy the sibling frontend (Cloudflare) so the live UI reads the new `{campaign, warnings[]}` shape and shows the new field.
3. Human-UAT per the 12-04 checkpoint (set 70 → warning; 50 → no warning; 120 → rejected; persists on reload).

⚠ Backend and frontend must deploy together (or backend→frontend in quick succession): the CampaignWriteResponse change breaks the currently-deployed frontend's campaign create/edit until the new frontend ships.

## Self-Check: PASSED (code complete; deploy/UAT intentionally deferred)
