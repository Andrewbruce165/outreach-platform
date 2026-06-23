---
phase: 08-pool-management-and-even-distribution
plan: 04
subsystem: ui
tags: [react, tanstack-query, openapi-typescript, telegram, pool-management, cross-repo]

# Dependency graph
requires:
  - phase: 08-pool-management-and-even-distribution (Plan 03)
    provides: "POST/DELETE /campaigns/{id}/senders attach-detach endpoints with SENDER_LOCK_CONFLICT / MIN_POOL_GUARD / DETACH_BLOCKED_PENDING 409 contracts"
  - phase: 08-pool-management-and-even-distribution (Plan 02)
    provides: "rebalance_on_attach even-split that the running-campaign attach path triggers"
provides:
  - "Interactive Senders/Пул panel on the campaign page (add via multiselect/chips, remove per row) for draft/paused/running campaigns"
  - "Fixed SENDER_LOCK_CONFLICT formatter (array-based conflicts[]) + new MIN_POOL_GUARD / DETACH_BLOCKED_PENDING human-readable codes"
  - "Regenerated openapi.json + src/types/api.ts including the two pool /senders endpoints"
  - "GET /api/v1/senders now exposes locked_by_campaign_id / locked_by_campaign_name so the add-picker can disable locked accounts (UAT-driven backend addition)"
affects: [phase-09-cold-contact-failover, phase-10-pool-visibility]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Cross-repo plan: backend generated artifact (openapi.json) commits to Andrewbruce165/outreach-platform; frontend (panel, error-codes, types) commits to sibling AGS-Venture-Lab/aimly-tg-outreach"
    - "Frontend pool mutations mirror lifecycleMut: useMutation + qc.invalidateQueries(['campaign', id]) onSuccess + setActionError(errMsg(e)) onError, reusing the existing actionError banner"
    - "Server-authoritative UI: add/remove disabling for locked senders is UX-only; all lock/guard/isolation enforcement stays server-side (Plan 03)"

key-files:
  created: []
  modified:
    - "app/routers/senders.py — GET /senders exposes locked_by_campaign_id/name (UAT fix)"
    - "app/schemas/__init__.py — Sender schema gains lock-state fields"
    - "lovable-handoff/openapi.json — regenerated with the two pool /senders endpoints + sender lock fields"
    - "tests/test_pool_endpoints.py — test_list_senders_exposes_lock_state (UAT fix)"
    - "src/routes/_authenticated/campaigns.$id.tsx (sibling) — interactive Senders/Пул panel"
    - "src/lib/error-codes.ts (sibling) — MIN_POOL_GUARD + DETACH_BLOCKED_PENDING + array-based SENDER_LOCK_CONFLICT"
    - "src/types/api.ts (sibling) — regenerated types for the two pool endpoints"

key-decisions:
  - "GET /api/v1/senders now exposes lock state (locked_by_campaign_id/name); D-02 lock semantics unchanged — the change only surfaces the already-existing lock to the add-picker"
  - "Disabling locked pills in the add-picker is a UX nicety, not a security control — server still enforces the 409 SENDER_LOCK_CONFLICT"

patterns-established:
  - "Pattern 1: pool attach/detach mutations reuse the existing lifecycleMut shape and the existing actionError banner — no new error surface"
  - "Pattern 2: human-readable 409 mapping lives in error-codes.ts CODE_MAP, reading only workspace-scoped detail.conflicts[].campaign_name (no internals leaked)"

requirements-completed: [POOL-09]

# Metrics
duration: ~3h (incl. cross-repo reconcile against Lovable + human UAT + UAT-driven backend fix)
completed: 2026-06-23
---

# Phase 8 Plan 04: Frontend Pool Panel Summary

**Interactive Senders/Пул panel on the campaign page (attach/detach via multiselect/chips, locked-sender display, human-readable 409s), fixed array-based SENDER_LOCK_CONFLICT formatter, and regenerated OpenAPI handoff — closing POOL-09.**

## Performance

- **Duration:** ~3h (spans cross-repo reconcile against 16 concurrent Lovable commits + human UAT + one UAT-driven backend fix)
- **Started:** 2026-06-23
- **Completed:** 2026-06-23
- **Tasks:** 4 (3 auto + 1 blocking human-verify checkpoint)
- **Files modified:** 7 (4 backend repo, 3 sibling repo)

## Accomplishments
- Upgraded the read-only Senders section on the campaign page into an interactive Senders/Пул panel: add via multiselect/chips, remove per row, works for draft/paused/running campaigns (D-10/D-11).
- Fixed the latent SENDER_LOCK_CONFLICT formatter to read the array-based `detail.conflicts[]` contract the backend actually emits, and added MIN_POOL_GUARD + DETACH_BLOCKED_PENDING human-readable mappings.
- Regenerated `openapi.json` + `src/types/api.ts` via the export-handoff script (no hand-editing) so the frontend types include POST/DELETE `/campaigns/{id}/senders`.
- Human UAT (Task 4) passed in-browser against the deployed panel; user replied "approved".
- UAT-driven fix: surfaced the existing sender lock on GET /senders so the add-picker disables senders locked by a running campaign instead of offering them as free (then 409-ing confusingly).

## Task Commits

Each task was committed atomically across the two repos:

1. **Task 1: Regenerate OpenAPI handoff** — backend `7fcd188` (chore); sibling `src/types/api.ts` regenerated in sibling `f5e5a53`
2. **Task 2: Fix error-codes.ts** — sibling (MIN_POOL_GUARD, DETACH_BLOCKED_PENDING, array-based SENDER_LOCK_CONFLICT)
3. **Task 3: Interactive Senders/Пул panel** — sibling `campaigns.$id.tsx`; panel commit rebased onto origin/main and pushed as sibling `cfefc62` after the Lovable reconcile
4. **Task 4: Human UAT (blocking checkpoint)** — user approved after the 6 in-browser steps; no code in this task itself

**UAT-driven fix (during the checkpoint):**
- Backend `060fee9` — expose sender lock state on GET /senders + `test_list_senders_exposes_lock_state` + regenerated handoff
- Sibling `b7b7669` — disable locked pills in the add-picker (pushed)

**Plan metadata:** committed with this SUMMARY (docs: complete plan)

## Files Created/Modified

**Backend repo (Andrewbruce165/outreach-platform):**
- `app/routers/senders.py` — GET /senders now computes and returns `locked_by_campaign_id` / `locked_by_campaign_name` per sender (UAT fix)
- `app/schemas/__init__.py` — Sender response schema gains the two lock-state fields
- `lovable-handoff/openapi.json` — regenerated; includes the two pool `/senders` endpoints and the sender lock fields (produced solely by the export-handoff script)
- `tests/test_pool_endpoints.py` — added `test_list_senders_exposes_lock_state`

**Frontend sibling repo (AGS-Venture-Lab/aimly-tg-outreach):**
- `src/routes/_authenticated/campaigns.$id.tsx` — interactive Senders/Пул panel (attachMut/detachMut, multiselect/chips add, per-row remove, locked display, human-readable 409s via the existing actionError banner)
- `src/lib/error-codes.ts` — MIN_POOL_GUARD + DETACH_BLOCKED_PENDING keys; SENDER_LOCK_CONFLICT rewritten to read `detail.conflicts[].campaign_name`
- `src/types/api.ts` — regenerated types for the two pool endpoints + sender lock fields

## Decisions Made
- **Expose lock state on GET /senders.** UAT surfaced that the add-picker offered senders locked by a running campaign as if free, producing a confusing 409 only on click. Fixed by returning `locked_by_campaign_id`/`locked_by_campaign_name` from GET /senders and disabling those pills in the picker. D-02 lock semantics are unchanged — the change only surfaces the already-existing lock; the server still enforces the 409 SENDER_LOCK_CONFLICT.
- **Disabling locked pills is UX, not authz.** Per the plan's threat model T1, all isolation/lock/guard enforcement remains server-side (Plan 03); the disabled control is a convenience only.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] Expose sender lock state on GET /senders (UAT-driven backend change)**
- **Found during:** Task 4 (human UAT checkpoint)
- **Issue:** The add-picker listed every workspace sender as addable, including senders locked by another running campaign. Adding one only failed at click-time with a 409 — confusing, and not in the original plan scope (the plan assumed the panel could rely on `attached_senders[].locked_by_campaign_name`, which only covers already-attached senders, not the add-list).
- **Fix:** Backend GET /api/v1/senders now computes and returns `locked_by_campaign_id`/`locked_by_campaign_name` per sender; the sibling add-picker disables locked pills. Lock semantics (D-02) untouched — only the existing lock is surfaced.
- **Files modified:** `app/routers/senders.py`, `app/schemas/__init__.py`, `lovable-handoff/openapi.json`, `tests/test_pool_endpoints.py` (backend `060fee9`); `src/routes/_authenticated/campaigns.$id.tsx` (sibling `b7b7669`)
- **Verification:** Backend pool tests 8/8 pass via the test-overlay; sibling `tsc --noEmit` clean; re-verified in-browser before the user re-approved.
- **Committed in:** `060fee9` (backend) + `b7b7669` (sibling)

---

**Total deviations:** 1 auto-fixed (1 missing-critical, UAT-driven).
**Impact on plan:** The fix removed a confusing add-picker behaviour and was necessary for POOL-09 to behave correctly; no scope creep beyond surfacing the existing lock. D-02/D-10/D-11/D-12 decisions all held.

## Issues Encountered
- **Cross-repo reconcile against concurrent Lovable work.** While Task 3 was in flight, Lovable pushed 16 commits to the sibling `origin/main`. The panel commit was rebased onto `origin/main` and pushed (sibling `cfefc62`) with no Lovable commit dropped — verified before continuing.
- **Unrelated backend merge.** An external `feat(warmup): full-mesh parallel dialogues per account` commit (`ddde675`) was merged into the backend repo from `origin/main` during this work. It is unrelated to POOL-09 and was left as-is.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- POOL-09 closed; Phase 8 (Pool Management & Even Distribution) is fully delivered (4/4 plans).
- Pool attach/detach + even distribution + interactive panel are live; ready for Phase 9 (Cold-Contact Failover), which builds on the multi-sender pool established here.

## Self-Check: PASSED

- `app/routers/senders.py` — FOUND
- `app/schemas/__init__.py` — FOUND
- `lovable-handoff/openapi.json` — FOUND
- `tests/test_pool_endpoints.py` — FOUND
- Commit `7fcd188` (Task 1 handoff regen) — FOUND
- Commit `060fee9` (UAT lock-exposure fix) — FOUND

---
*Phase: 08-pool-management-and-even-distribution*
*Completed: 2026-06-23*
