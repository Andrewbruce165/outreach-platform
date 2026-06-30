---
phase: 17-sender-side-resolve-ladder-with-username-capture-and-import-fallback
plan: 02
subsystem: contact-resolution
tags: [checker, username-capture, confidence-gated-cache, resolve-ladder, igor-fix]

# Dependency graph
requires:
  - phase: 17-sender-side-resolve-ladder-with-username-capture-and-import-fallback
    plan: 01
    provides: "RED tests test_username_capture_in_resolve_phone/_import_fallback, test_confidence_gated_cache_checker_read (test_checker.py); GREEN persistence contract test_captured_username_persisted (test_contact_check_worker.py)"
  - phase: 14-reliable-contact-resolution
    provides: "contacts.tg_probe_state/tg_confidence (mig 034), resolve_phone_with_fallback, _apply_results suspect/clean finalization, contacts.tg_username_resolved (mig 013)"
provides:
  - "resolve_phone_with_fallback returns 'username' (the public/transferable @handle) on both registered paths and uniform None elsewhere — the sender's cheap tier-2 ResolveUsername resolve (consumed by worker:875 -> contacts.tg_username_resolved)"
  - "_lookup_cache confidence-gates the is_registered=false bucket: a suspect/low-confidence cached false is suppressed (returns None -> forces live re-resolve), the Igor cross-contamination root-cause fix"
affects: [17-03-sender-resolve-ladder, 17-04-block-capture-and-docs]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Confidence-gated cache read: gate ONLY the negative bucket against the matching contacts row's tg_probe_state/tg_confidence (D-12) — no schema change; positives always served, cache never deleted"
    - "Uniform return-dict shape so a downstream res.get('username') never KeyErrors (key present, value None on every not-registered path)"

key-files:
  created: []
  modified:
    - app/services/checker.py

key-decisions:
  - "Suspect predicate: `tg_probe_state = 'suspect' OR tg_confidence IS DISTINCT FROM 'high'` — the IS DISTINCT FROM clause treats NULL confidence as not-trusted (a tg_confidence NULL contact suppresses the cached false), per Research OQ#1 conservative reading."
  - "Conservative OQ#1 cardinality: a phone may map to multiple contacts; if ANY matching contact is suspect/low-confidence we fall through to live resolve rather than trust a stale clean sibling."
  - "No migration — all storage columns already exist (contacts.tg_username_resolved mig 013, contacts_cache.username, contacts.tg_probe_state/tg_confidence mig 034). Confirmed `ls migrations/044*` empty."

requirements-completed: [SRLD-01, SRLD-02, SRLD-07]

# Metrics
duration: 5min
completed: 2026-06-30
---

# Phase 17 Plan 02: Checker Username Capture and Gated Read Summary

**The checker now captures the public, transferable `@username` on both registered resolve paths (`ResolvePhone` + `ImportContacts` fallback) and stops serving poisoned `is_registered=false` cache rows blind — a suspect/low-confidence cached false is suppressed so the live re-resolve actually happens (the Igor cross-contamination root-cause fix), all with no migration.**

## Performance

- **Duration:** ~5 min
- **Started:** 2026-06-30T16:26Z
- **Completed:** 2026-06-30
- **Tasks:** 2
- **Files modified:** 1 (`app/services/checker.py`)

## Accomplishments

- **SRLD-01/02 (Task 1) — username capture.** `resolve_phone_with_fallback` now returns `"username"`:
  - ResolvePhone success: `"username": getattr(user, "username", None)` (was discarded).
  - ImportContacts fallback success: `"username": getattr(imported_user, "username", None)` (was discarded).
  - Every `is_registered=False` return (invalid phone, resolve-non-empty-but-no-user, import failure, import empty) now carries `"username": None` — uniform shape so the worker's `res.get("username")` never KeyErrors.
  - The worker (`contact_check_worker.py:875`) already writes `res.get("username")` into `contacts.tg_username_resolved`; the CSV `contacts.username` is never written by the capture path (the only worker references to `c.username` are SELECT reads at :247/:289 and a local dict-comprehension at :832). The SRLD-02 persistence contract (`test_captured_username_persisted`) was already GREEN and stays GREEN.
- **SRLD-07 checker side (Task 2) — confidence-gated read.** `_lookup_cache` keeps the existing blind SELECT but, when the fetched row is `is_registered=false`, runs a correlated check against `contacts` in the same workspace; if ANY matching contact is `tg_probe_state='suspect'` OR `tg_confidence IS DISTINCT FROM 'high'`, it returns `None` (forces a live re-resolve). Clean+high-confidence false rows are still served; positive (`is_registered=true`) rows are served unchanged; no cache row is ever deleted.

## Exact change

- **Return-shape change:** added the `"username"` key to all six return sites of `resolve_phone_with_fallback` — `getattr(...)` value on the two registered paths (ResolvePhone success, ImportContacts success), literal `None` on the four not-registered paths.
- **Chosen suspect predicate:** `tg_probe_state = 'suspect' OR tg_confidence IS DISTINCT FROM 'high'`. The `IS DISTINCT FROM 'high'` clause is NULL-safe — a contact whose probe was never certified (`tg_confidence` NULL) counts as not-trusted and suppresses the cached false, matching D-12.
- **No migration:** verified `ls migrations/044*` is empty; all four storage columns pre-exist (mig 013 + mig 034).

## `-k` selectors (this plan)

| Req | File | `-k` selector | Result |
|-----|------|---------------|--------|
| SRLD-01 | test_checker.py | `username_capture` (×2) | GREEN |
| SRLD-02 | test_contact_check_worker.py | `captured_username` | GREEN (contract) |
| SRLD-07 (checker) | test_checker.py | `confidence_gated_cache` | GREEN |

## Task Commits

1. **Task 1: checker captures @username** — `305998b` (feat)
2. **Task 2: confidence-gate _lookup_cache false read** — `4906965` (fix)

## Files Created/Modified

- `app/services/checker.py` — `resolve_phone_with_fallback` returns `username` on all paths; `_lookup_cache` confidence-gates the `is_registered=false` bucket.
- `.planning/phases/17-.../deferred-items.md` — logged 8 out-of-scope RED failures (sibling-plan / parallel-effort ownership).

## Decisions Made

- **Predicate semantics:** gate ONLY the negative bucket; positives are never a contamination risk in this read, so they pass through unchanged. (`is_registered=true` cache rows are always served.)
- **OQ#1 cardinality (conservative):** a phone → many contacts; ANY suspect/low-confidence match suppresses the cached false. A stale clean sibling does not rescue a poisoned negative.
- **No schema change:** the confidence signal is read off `contacts.tg_probe_state`/`tg_confidence` (Phase 14), not a new `contacts_cache` column — D-12 explicitly recommends gating the READ against existing `contacts.*` columns.

## Deviations from Plan

None — plan executed exactly as written. Both tasks committed file-scoped (`app/services/checker.py` only); no production code outside the plan's single file was touched.

## Out-of-Scope Failures (NOT regressions)

The end-of-plan full suite ran **843 passed, 1 skipped, 8 failed**. All 8 failures are pre-existing RED tests owned by sibling plans / a parallel effort (logged in `deferred-items.md`), NOT caused by this plan's checker changes:

- `tests/test_send.py` ×5 (SRLD-03/04/05/06/07-sender) → **17-03** (sender side, `app/services/telegram.py` — explicitly off-limits to 17-02).
- `tests/test_send.py::test_user_blocked_records_event` + `tests/test_restriction_audit.py::test_block_rate_aggregate` (SRLD-08) → **17-04** (block capture; `sender_block_rate` helper not built — ImportError).
- `tests/test_warmup_worker.py::test_restricted_sender_excluded` (WARM-14) → **Phase 15 warmup**, an uncommitted parallel working-tree effort in `app/services/warmup.py` (unrelated to the checker).

This exactly matches the 17-01 baseline (10 RED + 2 GREEN created; 17-02 flips 3 of the 10 RED to GREEN, leaving 7 for 17-03/17-04) plus the one independent warmup RED from a parallel session.

## Known Stubs

None.

## Next Phase Readiness

- 17-03 (sender resolve ladder) now has the checker emitting `username` so its captured-handle tier-2 `ResolveUsername` has a real source (`contacts.tg_username_resolved`).
- 17-04 (block capture + docs) unaffected by this plan.
- Full suite: 851 tests run, 843 passed (checker SRLD-01/02/07 GREEN), 8 RED owned by sibling/parallel plans.

## Self-Check: PASSED

- `app/services/checker.py` — FOUND (modified)
- `17-02-SUMMARY.md` — written this run
- Commits `305998b`, `4906965` — to be verified below
