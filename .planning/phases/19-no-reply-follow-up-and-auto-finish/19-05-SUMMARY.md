---
phase: 19-no-reply-follow-up-and-auto-finish
plan: 05
subsystem: ui
tags: [openapi, campaign, follow-up, react, typescript, cross-repo]

# Dependency graph
requires:
  - phase: 19-02
    provides: the 4 campaign follow-up fields (follow_up_enabled/interval_hours/max_pings/auto_finish_hours) on the create/update/response schemas
  - phase: 19-04
    provides: FollowUpWorker state machine (auto-finish-first / ping-else) that consumes the campaign follow-up settings
provides:
  - Regenerated lovable-handoff/openapi.json exposing the 4 follow_up_* fields + no_reply status
  - Follow Up settings block (toggle + 3 bounded numeric inputs) on the campaign create and edit forms in the sibling frontend repo
  - Human-verified end-to-end Follow Up UI + persistence + bounds
affects: [frontend, campaign-form, phase-20]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Offline openapi regen via app.openapi() in the test container (Phase 16/18 precedent) — no un-gated prod deploy"
    - "Follow Up block mirrors the existing recontact / max_new_dialogs_per_day settings pattern (toggle + numeric inputs with min/max bounds)"

key-files:
  created: []
  modified:
    - "lovable-handoff/openapi.json (backend repo — 4 follow_up_* fields + no_reply status)"
    - "src/routes/_authenticated/campaigns.new.tsx (frontend repo — Follow Up block on create form)"
    - "src/components/EditCampaignModal.tsx (frontend repo — Follow Up block on edit form)"
    - "src/types-openapi.json + src/types/api.ts (frontend repo — regenerated TS types)"

key-decisions:
  - "D-08/D-12: form exposes Enable Follow Up toggle (default OFF) + ping interval (4–168h, default 24) + max pings (1–5, default 2) + auto-finish (24–720h, default 72), matching the D-12 bounds"
  - "Cross-repo isolation: openapi.json committed to Andrewbruce165/outreach-platform (a6644fd); the form + TS types committed to AGS-Venture-Lab/aimly-tg-outreach (f5b975e) — never mixed"

patterns-established:
  - "Follow Up controls disabled/greyed when the toggle is OFF, mirroring recontact"

requirements-completed: [NORP-13]

# Metrics
duration: ~35min
completed: 2026-07-03
---

# Phase 19 Plan 05: Surface Follow Up in the Product Summary

**Regenerated the openapi contract with the 4 follow_up_* fields + no_reply status and added the Follow Up settings block (toggle + interval/max-pings/auto-finish inputs) to the campaign create and edit forms, human-verified end-to-end.**

## Performance

- **Duration:** ~35 min (across two runs; continuation resumed at the human-verify gate)
- **Started:** 2026-07-03 (Task 1/2 prior run)
- **Completed:** 2026-07-03T09:59:16Z
- **Tasks:** 3 (2 auto + 1 checkpoint:human-verify)
- **Files modified:** 5 (1 backend, 4 frontend)

## Accomplishments
- lovable-handoff/openapi.json regenerated OFFLINE (app.openapi() in the test container) carrying follow_up_enabled / follow_up_interval_hours / follow_up_max_pings / auto_finish_hours on the campaign create/update/response schemas plus the no_reply conversation status.
- Follow Up settings block added to BOTH the create (campaigns.new.tsx) and edit (EditCampaignModal.tsx) campaign forms: Enable toggle (default OFF) + three bounded numeric inputs (interval 4–168h, max pings 1–5, auto-finish 24–720h) wired through the existing create/update mutation.
- TS API types regenerated so the 4 follow-up fields + no_reply status are typed (no `any`); tsc clean.
- Human verified the create/edit persistence, bounds enforcement, and live no_reply/ping/auto-finish flow — approved.

## Task Commits

1. **Task 1: Regenerate lovable-handoff/openapi.json (backend repo)** - `a6644fd` (feat) — Andrewbruce165/outreach-platform
2. **Task 2: Add the Follow Up block to the campaign form (sibling frontend repo)** - `f5b975e` (feat) — AGS-Venture-Lab/aimly-tg-outreach
3. **Task 3: Human-verify the Follow Up form + live no_reply flow** - checkpoint:human-verify (gate=blocking), approved by user — no commit

**Plan metadata:** committed with this SUMMARY (docs: complete plan) — tg-outreach repo only.

## Files Created/Modified
- `lovable-handoff/openapi.json` (backend) - API contract now exposes the 4 follow_up_* fields + no_reply status
- `src/routes/_authenticated/campaigns.new.tsx` (frontend) - Follow Up block on the create form
- `src/components/EditCampaignModal.tsx` (frontend) - Follow Up block on the edit form
- `src/types-openapi.json` + `src/types/api.ts` (frontend) - regenerated TS types

## Decisions Made
- Follow Up UI follows the established recontact / max_new_dialogs_per_day settings pattern (toggle + numeric inputs with surfaced min/max), keeping the campaign form consistent.
- Offline openapi regeneration reused the Phase 16/18 recipe (app.openapi() in the test container) to avoid an un-gated prod deploy.
- Cross-repo commit isolation strictly preserved: backend openapi change to outreach-platform, frontend form + types to aimly-tg-outreach.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required. Deploy note (from the plan): applying Phase 19 to prod requires rebuilding BOTH api and listener (`cd /root/apps/aimly/tg-outreach && git pull && docker compose up -d --build api listener`); the frontend deploys via Cloudflare.

## Next Phase Readiness
- Phase 19 (No Reply Follow-Up and Auto-Finish) is now COMPLETE across all 5 plans: schema (19-01), API + AI (19-02), listener/queue reply-revert + ping-cancel guards (19-03), FollowUpWorker state machine (19-04), and the product surface — openapi + campaign form (19-05).
- The Follow Up feature is fully wired backend-to-frontend and human-verified end-to-end. No blockers.

## Self-Check: PASSED

---
*Phase: 19-no-reply-follow-up-and-auto-finish*
*Completed: 2026-07-03*
