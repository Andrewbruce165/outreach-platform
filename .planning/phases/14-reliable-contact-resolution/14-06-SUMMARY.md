---
phase: 14-reliable-contact-resolution
plan: 06
subsystem: infra
tags: [telegram, checker, throttle, diagnostics, read-only-spike, resolve-phone, resolve-username]

# Dependency graph
requires:
  - phase: 14-05
    provides: flood-aware finalization (inline throttle signal + suspect rollback) — the corrected worker that would drain on GO
provides:
  - "Read-only pool-throttle diagnostic: phone-resolve is ALIVE (96-98% on control set), not pool-dead"
  - "Evidence the parked checkers' 0% in 14-04 was transient (burn recovered after rest), not structural"
  - "Finding: @username resolve is dead on the 2 parked checkers (returns 0 users for @telegram/@durov)"
  - "Rate-trigger analysis: burst onset (pos 47-49) is ABOVE burst_cap=30 — one batch is safe; gap is no per-checker inter-batch cooldown"
  - "Conditional GO/NO-GO recommendation surfaced at a blocking human-verify gate"
affects: [14-reactivation-followup, checker-pool-management]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "READ-ONLY prod diagnostic via probe_control (no cache read/write, no contacts mutation) + resolve_phone_with_fallback (self-cleaning address book)"
    - "Scope-restricted probe: only parked checkers touched, never live role='sender' accounts"

key-files:
  created:
    - .planning/notes/checker-pool-throttle-spike.md
  modified: []

key-decisions:
  - "Conditional GO on phone-resolve drain (resolve is healthy 96-98%, burn was reversible); requires per-checker inter-batch cooldown or >=2-checker rotation before draining 14k"
  - "NO-GO on @username as a fallback on the 2 parked checkers — their username-resolve path is shadow-throttled"
  - "Scope-limited: only 2 parked checkers probed (user restriction) — cannot prove pool-wide alive; recommend a fresh-account control probe as gated follow-up for an unconditional GO"

patterns-established:
  - "Read-only throttle spike: probe control set live, record hit-rate-vs-position curve, judge onset vs burst_cap, verify control rows + cache count unchanged"

requirements-completed: []  # RESV-01/RESV-02 are exercised diagnostically here but NOT finalized — re-activation/drain deferred to a verdict-gated follow-up; orchestrator owns requirement marking.

# Metrics
duration: ~17min
completed: 2026-06-26
---

# Phase 14 Plan 06: Checker Pool-Throttle Diagnostic Spike Summary

**READ-ONLY prod spike proves phone-resolve is ALIVE (parked checkers hit 96-98% on the 49 known-live control set, not 0%) — the 14-04 collapse was transient burn, not pool death; @username-resolve is dead on these accounts; conditional GO with a per-checker inter-batch cooldown requirement.**

## Performance

- **Duration:** ~17 min
- **Started:** 2026-06-26T15:14:00Z
- **Completed:** 2026-06-26T15:31:00Z
- **Tasks:** 2 auto tasks complete; stopped at Task 3 blocking human-verify gate
- **Files modified:** 1 created (findings note)

## Accomplishments
- Ran a READ-ONLY diagnostic against the 2 parked checkers (`sender-7979031303`, `sender-8364639216`) over the 49 known-live control set; both resolved **47/49 (96%)** and **48/49 (98%)** — directly refuting the "phone-resolve dead pool-wide" hypothesis from 14-04.
- First-empty positions (47, 49) land exactly in the calibrated ~45-50 burst-onset window — a normal soft burst, not a shadow-ban (which gives ~0.07% live) and not the 14-04 collapse-at-20-30.
- Discovered the parked checkers' burn is **reversible**: same accounts that gave 0% a day ago now resolve 96-98% after rest.
- Found `ResolveUsername` is shadow-throttled on both checkers (returns 0 users for `@telegram`/`@durov`) → @username is NOT a viable fallback on these accounts.
- Rate-trigger analysis (Q3): burst onset is ABOVE burst_cap=30, so one batch is safe; the real gap is no per-checker inter-batch cooldown (multiple back-to-back ticks on one checker = cumulative burst overrun, the likely 14-04 cause).
- Wrote `.planning/notes/checker-pool-throttle-spike.md` with the per-account hit-rate table, position-of-first-empty curve, @username result, rate-trigger analysis, an explicit conditional GO/NO-GO, the scope-limitation caveat, and a link to checker-false-negatives.md §Часть 2.

## Task Commits

1. **Task 1: Read-only diagnostic spike (phone-resolve, @username, rate-trigger)** — evidence-only; the spike scripts live in scratchpad (NOT committed per plan); produced no repo file. Verify `control rows == 49` PASS.
2. **Task 2: Findings note + GO/NO-GO** — `<note-commit>` (docs)

**Plan metadata:** orchestrator owns STATE.md/ROADMAP.md (not written here).

## Files Created/Modified
- `.planning/notes/checker-pool-throttle-spike.md` - Diagnostic findings: per-account hit-rates (96%/98%), position curve, @username dead, rate-trigger analysis, conditional GO/NO-GO, scope caveat.

## Decisions Made
- **Conditional GO on phone-resolve** — resolve is healthy and burn is reversible, so a guarded drain is justified, but a follow-up plan MUST add a per-checker inter-batch cooldown (or guarantee ≥2-checker rotation) and start with a small stage; otherwise 14-04's cumulative-burst collapse recurs.
- **NO-GO on @username fallback on these checkers** — their username path is shadow-throttled; a username pivot needs a separate healthy account with its own username health-probe.
- **Scope honored (user restriction):** only the 2 parked checkers were probed; no live `role='sender'` account was touched. Documented that this cannot fully prove pool-wide aliveness; recommended a fresh-account control probe as the gated follow-up for an unconditional GO.

## Deviations from Plan

The plan text said to also probe "several role='sender' accounts" for a pool-wide comparison. This was OVERRIDDEN by a binding SCOPE_OVERRIDE in the execution prompt (probe ONLY the 2 parked checkers, never any live outreach account, to avoid throttle risk to live senders). The consequence — the spike cannot fully distinguish "phone-resolve dead pool-wide" from "these 2 checkers specifically burned" — is documented explicitly in the note's Scope and GO/NO-GO sections, and a fresh-account probe is recommended as the gated follow-up rather than claiming an unsupported pool-wide verdict.

This is not an auto-fix deviation; it is an instructed scope restriction, recorded for traceability.

## Issues Encountered
- The spike script failed to import `app` when run from `/tmp` (Python `sys.path[0]` = script dir, not cwd). Resolved by running with `PYTHONPATH=/app` inside the `outreach-platform-api` container. No code change.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Decision artifact ready: `.planning/notes/checker-pool-throttle-spike.md`.
- Awaiting the Task 3 blocking human-verify GO/NO-GO. On GO, a SEPARATE gap-closure plan must implement a guarded re-activation + staged 14k drain (with the inter-batch cooldown / rotation guard from Q3); on NO-GO, pivot per the note (most likely: a fresh-account control probe first, since phone-resolve itself is healthy).
- Re-activation and the 14k drain are NOT performed by this plan.

## Known Stubs
None — this is a diagnostic plan with no code/UI changes; the only artifact is a findings note.

## Self-Check: PASSED
- FOUND: .planning/notes/checker-pool-throttle-spike.md
- FOUND: .planning/phases/14-reliable-contact-resolution/14-06-SUMMARY.md
- Read-only invariant held (verified by SELECT): 49 control rows still registered, contacts_cache unchanged (5), senders unchanged, 0 new restriction events.

---
*Phase: 14-reliable-contact-resolution*
*Completed: 2026-06-26*
