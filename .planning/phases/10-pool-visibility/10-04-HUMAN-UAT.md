# 10-04 Human-UAT — Pool Badge & Account Event-List (POOLV-03 / POOLV-04)

**Status: PENDING — closed on trust, NOT yet performed.**

The plan was finalized on user trust so execution could complete. The 6 in-browser
verification steps below were **NOT actually run**: the frontend is committed to the
sibling repo `AGS-Venture-Lab/aimly-tg-outreach` (commit `566dce6`) but **not deployed**,
so there was no live UI to exercise. Run these steps after a frontend deploy
(Cloudflare/wrangler) at https://aimly.agsventurelab.com.

| # | Step | Expected | Status |
|---|------|----------|--------|
| 1 | Attach ≥2 senders to a running campaign; open the campaign page | Badge 🟢 "Пул активен" | ⬜ pending |
| 2 | Force one sender into `spam_limited` with a future `restricted_until` (staging UPDATE or @SpamBot-reconcile); reload | Badge 🟡 "K из N на паузе · до проверки в T"; that sender shows a restriction chip in the attached list, the others stay active | ⬜ pending |
| 3 | Force ALL attached senders into a restriction | Badge 🔴 "Весь пул на паузе" | ⬜ pending |
| 4 | Clear the restriction | Badge returns to 🟢 "Пул активен" | ⬜ pending |
| 5 | Open "История ограничений" for a sender with logged events | Mini-list newest-first: type / source / `restricted_until` + one-line activity-slice summary; freeze→extension→clear reads as clean chronology (no 37-tick noise, D-01) | ⬜ pending |
| 6 | Confirm a cleared/healthy account shows no spurious "paused" state, history is readable, and only the current workspace's senders/events are visible | No cross-tenant leakage; healthy account clean | ⬜ pending |

## What IS verified (code-level, this session)

- `tsc --noEmit` clean across both modified pages (campaign + accounts).
- `lovable-handoff/openapi.json` contains `pool_health` + `/senders/{slug}/restriction-events` (regenerated via export-handoff, not hand-edited).
- Badge derives all 3 states client-side from numeric `pool_health`; event-list fetches the HLTH-03 endpoint (newest-first as returned by the backend).
- Backend live (api rebuilt, migration 030 applied).

## What is NOT verified

- Any visual rendering / interaction in a browser (steps 1–6 above).
- Live pool_health state transitions reflected in the badge.
- Cross-tenant isolation observed in the UI (server-side scoping IS enforced per Plan 03; the UI observation is what is pending).

**Acceptance:** closed on trust 2026-06-24. Real human-UAT to follow after frontend deploy.
