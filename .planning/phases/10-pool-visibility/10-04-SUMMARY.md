---
phase: 10-pool-visibility
plan: 04
subsystem: pool-visibility
tags: [wave-4, frontend, cross-repo, pool-badge, restriction-events, poolv-03, poolv-04, human-uat]
dependency_graph:
  requires:
    - "10-03 (CampaignResponse.pool_health + attached_senders restriction enrichment + GET /senders/{slug}/restriction-events)"
    - "08-04 (cross-repo pattern: export-handoff regen, SendersPanel on campaign page, sibling-repo commit discipline)"
  provides:
    - "Campaign-page 3-state pool badge (green/yellow/red) derived on the frontend from numeric pool_health (POOLV-03)"
    - "Per-sender restriction chips on the attached pool (POOLV-02 consumer)"
    - "Account-page restriction-event mini-list (newest-first) off the HLTH-03 endpoint (POOLV-04)"
    - "Regenerated lovable-handoff/openapi.json + types/api.ts with pool_health + restriction-events"
  affects:
    - "End of Phase 10 frontend surface — aggregate dashboard deferred to backlog (D-11)"
tech_stack:
  added: []
  patterns:
    - "3-state badge derived client-side from numeric pool_health (API stays presentation-free; mirrors 08-04 numeric-contract philosophy)"
    - "react-query keyed by slug for the per-account event-list (useQuery(['restriction-events', slug]))"
    - "OpenAPI regenerated via scripts/export-handoff.sh (never hand-edited — Pitfall 5), types copied into sibling src/types/api.ts as 08-04 did"
    - "cross-repo commit discipline: openapi/types → Andrewbruce165/outreach-platform; frontend components → AGS-Venture-Lab/aimly-tg-outreach"
key_files:
  created: []
  modified:
    - lovable-handoff/openapi.json
    - lovable-handoff/types/api.ts
    - "SIBLING aimly-tg-outreach: src/routes/_authenticated/campaigns.$id.tsx"
    - "SIBLING aimly-tg-outreach: src/routes/_authenticated/accounts.tsx"
    - "SIBLING aimly-tg-outreach: src/types/api.ts"
decisions:
  - "Badge derived on the frontend from numeric pool_health (no server color field) — paused==0 green, 0<paused<total yellow, paused==total&&total>0 red"
  - "OQ#4 wording: partial/all-paused badge says 'до проверки в T' (recheck horizon), not 'возобновится в T'"
  - "Account-page event-list lives in a per-row 'История ограничений' modal (accounts.tsx is a fleet table, no dedicated sender detail route) — keyed by slug, newest-first as the backend already returns"
  - "Per-sender restriction chips added to the existing 08-04 SendersPanel attached list (consumes POOLV-02 enrichment) so partial pause shows WHICH senders are paused"
metrics:
  duration: ~12min
  completed: 2026-06-24
  tasks: 1 of 2 (Task 2 = blocking human-UAT checkpoint, awaiting reviewer)
  files: 5
---

# Phase 10 Plan 04: Frontend Pool Badge & Event-List Summary

Shipped the Phase-10 mini-UI (D-11) across the two repos: a 3-state pool badge on the campaign page that makes partial pause visible (🟡 "K из N на паузе до проверки в T"), per-sender restriction chips on the attached pool, and a newest-first restriction-event mini-list on the account page — all derived on the frontend from the presentation-free numeric `pool_health` + the HLTH-03 history endpoint shipped in Plan 03. Backend was rebuilt and migration 030 applied so the regenerated handoff carries the new contract. **Task 2 is a blocking human-UAT checkpoint — NOT self-certified.**

## What Was Built

### Task 1 — openapi regen + components (backend commit `1fed6c0`, sibling commit `566dce6`)

**Step 1 — handoff regen (backend repo, commit `1fed6c0`):**
- `docker compose up -d --build api` rebuilt the api image (restart does not pick up code changes per CLAUDE.md); migration `030_sender_restriction_events` auto-applied at start (confirmed in api logs).
- `scripts/export-handoff.sh` regenerated `lovable-handoff/openapi.json` (booted db+api, scraped `/openapi.json` inside the container, validated the "Outreach" title) and `lovable-handoff/types/api.ts` via `openapi-typescript@7`. UI-SPEC drift check passed (39/39 endpoints). **Not hand-edited** (Pitfall 5).
- `openapi.json` now contains `pool_health` (under `CampaignResponse`) and the `/api/v1/senders/{slug}/restriction-events` path; types carry `PoolHealth` + `RestrictionEventResponse` schemas.
- Synced the regenerated types into the sibling repo `src/types/api.ts` (the 08-04 precedent — the sibling keeps its own copy of the generated file).

**Step 2 — campaign-page badge + chips (sibling, commit `566dce6`):**
- `campaigns.$id.tsx`: new `PoolBadge` component placed next to `StatusPill` in the header. Derives the 3 states **on the frontend** from numeric `pool_health`: `paused===0` → 🟢 "Пул активен"; `0<paused<total` → 🟡 "{paused} из {total} на паузе · до проверки в {T}"; `paused===total&&total>0` → 🔴 "Весь пул на паузе". Renders nothing for an empty pool (`total===0`). Uses existing tokens (`pill--green/orange/red`, `pill__dot`, `--success/--warning/--danger`). `earliest_resume_at` framed as a recheck horizon (OQ#4: "до проверки в T").
- New `RestrictionChip` on each attached-sender `<li>` in the existing 08-04 `SendersPanel`, reading the POOLV-02 enrichment (`attached_senders[].restriction_status` / `restricted_until`) so a partial pause shows WHICH senders are paused (spam_limited → orange "Спам-лимит", frozen → red "Заморожен", with "до {T}").

**Step 3 — account-page event-list (sibling, commit `566dce6`):**
- `accounts.tsx`: new `RestrictionHistoryModal` opened from a per-row "История ограничений" action (the accounts page is a fleet table — no dedicated sender route, so a slug-keyed modal is the natural host). `useQuery(['restriction-events', slug])` fetches `GET /api/v1/senders/{slug}/restriction-events` (workspace-scoped, newest-first as the backend already returns). Each row shows an event-type pill (spam_limited/frozen/flood_wait/extension/cleared/banned/privacy_restricted), the source (queue/@SpamBot/antispam-signal), the event time, `restricted_until` ("до проверки в T"), a one-line activity-slice summary ("12 отпр./ч · 138 отпр./24ч · 138 уник. контактов/24ч") and the raw_text. A freeze→extension→clear sequence reads as a clean chronology (D-01 — no per-tick noise, enforced server-side in Plan 02).

## Verification

| Check | Result |
|-------|--------|
| api rebuilt + migration 030 applied | ok (api log: `[migrate] OK 030_sender_restriction_events`) |
| `grep pool_health lovable-handoff/openapi.json` | present |
| `grep restriction-events lovable-handoff/openapi.json` | present |
| `PoolHealth` + `RestrictionEventResponse` in types | present |
| sibling `tsc --noEmit` | exit 0 (TSC_CLEAN) — after both campaign + account changes |
| 3 badge branches (green/yellow/red) in component | present |
| event-list fetches restriction-events endpoint, newest-first | present (backend ordering, react-query consumes as-is) |
| openapi/types produced by export-handoff.sh, not hand-edited | confirmed |

## Deviations from Plan

None — plan executed as written. The account event-list is hosted in a per-row modal rather than a dedicated sender detail page because `accounts.tsx` is a fleet table with no `/accounts/$slug` route; this is a presentation choice, not a contract deviation (the endpoint, slug key, and newest-first ordering are exactly per plan).

## Deferred Issues

None. Pre-existing backend wide-suite failures noted in `.planning/phases/10-pool-visibility/deferred-items.md` (Plan 10-02) are out of scope and untouched by this frontend plan.

## Known Stubs

None. The badge reads live `pool_health` from `GET /campaigns/{id}`; the event-list reads the real `sender_restriction_events` table via the HLTH-03 endpoint. Both are fully wired to Plan-03 backend, not stubs.

## Self-Check: PASSED
- FOUND: lovable-handoff/openapi.json (pool_health + restriction-events)
- FOUND: SIBLING src/routes/_authenticated/campaigns.$id.tsx (PoolBadge + RestrictionChip)
- FOUND: SIBLING src/routes/_authenticated/accounts.tsx (RestrictionHistoryModal)
- FOUND: SIBLING src/types/api.ts (PoolHealth, RestrictionEventResponse)
- FOUND: backend commit 1fed6c0
- FOUND: sibling commit 566dce6

## Human-UAT (Task 2) — PENDING

Task 2 is a `checkpoint:human-verify` (blocking, `auto_advance: false`). NOT self-certified. The reviewer must manually exercise the 6 steps (badge green→yellow→red→green, per-sender chips, account event-list chronology, no cross-tenant leakage) per the plan's `how-to-verify` and reply "approved". Backend is live (api rebuilt this session); the deployed frontend may need a sibling-repo deploy (Cloudflare/wrangler) for the reviewer to see it at https://aimly.agsventurelab.com.
