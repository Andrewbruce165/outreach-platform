---
phase: 17-sender-side-resolve-ladder-with-username-capture-and-import-fallback
plan: 03
subsystem: contact-resolution
tags: [sender, resolve-ladder, resolve-username, import-contacts, no-resolvephone, confidence-gated-cache, barter-fix]

# Dependency graph
requires:
  - phase: 17-sender-side-resolve-ladder-with-username-capture-and-import-fallback
    plan: 01
    provides: "RED tests test_resolve_ladder_no_sender_resolvephone / test_import_gate_registered_only / test_lazy_import_no_delete_on_sender / test_stale_username_fallthrough / test_confidence_gated_cache_sender_read (tests/test_send.py); mock_telethon_client.calls request-type introspection; _resolved/_imported/_raises/_seed_contact helpers"
  - phase: 17-sender-side-resolve-ladder-with-username-capture-and-import-fallback
    plan: 02
    provides: "checker persists captured @username to contacts.tg_username_resolved (worker:875) — the real source for tier-2 ResolveUsername; suspect predicate `tg_probe_state='suspect' OR tg_confidence IS DISTINCT FROM 'high'` (shared)"
  - phase: 14-reliable-contact-resolution
    provides: "contacts.tg_probe_state/tg_confidence (mig 034), contacts.tg_username_resolved (mig 013)"
provides:
  - "resolve_contact is the 3-tier sender ladder: cache(access_hash) → ResolveUsername(captured @username) → ImportContacts(gated on tg_status='registered') — the sender's OWN ResolvePhone is GONE (D-01), structurally fixing the Barter-ВЭД false-negative class"
  - "_load_contact_verdict(workspace_id, phone) → {tg_status, captured_username} — shared input for tier-2 + the tier-3 import gate, prefers a registered row"
  - "stale-username fall-through contract: _resolve_username returns {\"stale_username\": True} on UsernameNotOccupied/Invalid (NEVER caches/finalizes False); resolve_contact routes it to the import tier (D-09)"
  - "_get_cached_contact confidence-gates BOTH the per-sender and cross-sender is_registered=false reads — a suspect-source false no longer short-circuits the sender (D-12)"
affects: [17-04-block-capture-and-docs]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "3-tier sender resolve ladder asserted by client.calls request-type introspection (which RPCs fire, in order) — ResolvePhone must be ABSENT, not just unused"
    - "stale-username fall-through via a {stale_username: True} sentinel dict (not an exception, not a False finalization) so the import tier picks it up"
    - "Confidence-gated false cache read shared with 17-02: gate the negative bucket against the matching contacts row, never DELETE the cache row"

key-files:
  created: []
  modified:
    - app/services/telegram.py

key-decisions:
  - "Ladder order: cache(access_hash) → ResolveUsername(captured) → ImportContacts(registered-gated). The sender's own ResolvePhone is REMOVED entirely (D-01) — including its import — because it gave the false negatives in the live Barter-ВЭД incident (22 live RU mobiles)."
  - "ResolvePhoneRequest import REMOVED from telegram.py (no remaining call; the two surviving references are comments). The checker's ResolvePhone lives in checker.py and is untouched."
  - "Stale-username signal contract: {\"stale_username\": True} — a sentinel dict, not False, not a raise. For a PHONE key it falls through to the import tier; for a '@handle' identity-key contact (no phone to import) it maps to {is_registered: False} (the handle was the only identity)."
  - "tier-3 import returning no users → {is_registered: False} WITHOUT caching False — finalization is the checker's job (D-09 semantics). The number was tagged registered, so an empty import is more likely privacy/transient."
  - "Suspect predicate `tg_probe_state = 'suspect' OR tg_confidence IS DISTINCT FROM 'high'` — identical to 17-02's _lookup_cache gate (NULL confidence counts as not-trusted)."

requirements-completed: [SRLD-03, SRLD-04, SRLD-05, SRLD-06, SRLD-07]

# Metrics
duration: 6min
completed: 2026-06-30
---

# Phase 17 Plan 03: Sender Resolve Ladder Summary

**The sender's `resolve_contact` is rebuilt into the 3-tier ladder — cache(access_hash) → `ResolveUsername`(captured @username) → `ImportContacts`(gated on the checker verdict `registered`) — with the sender's OWN `ResolvePhone` removed entirely (D-01). A stale captured username falls through to the import tier instead of finalizing `not_registered` (D-09), and a suspect-source `is_registered=false` cache row no longer short-circuits the sender (D-12). This structurally fixes the live Barter-ВЭД class where the sender's own ResolvePhone falsely rejected 22 live RU mobiles.**

## Performance

- **Duration:** ~6 min
- **Started:** 2026-06-30T16:36Z
- **Completed:** 2026-06-30
- **Tasks:** 3
- **Files modified:** 1 (`app/services/telegram.py`)

## Accomplishments

- **Task 1 — `_load_contact_verdict` helper.** New private async method reading the existing `contacts.tg_status` + `contacts.tg_username_resolved` columns for a phone (NO migration). `ORDER BY (tg_status = 'registered') DESC, updated_at DESC LIMIT 1` prefers a registered row when a phone maps to multiple contacts. Returns `{"tg_status", "captured_username"}` — the shared input for tier-2 (captured username) and the tier-3 import gate.
- **Task 2 — 3-tier ladder (SRLD-03/04/05).** Rewrote the `resolve_contact` phone-key tail:
  - **Tier-2:** if a captured `@username` exists → `ResolveUsername`; on `is_registered` cache the access_hash under the PHONE key (follow-up sends are phone-cache hits) and return.
  - **Tier-3:** `ImportContacts` GATED on `tg_status == 'registered'` (D-03/D-11); the sender KEEPS the contact — NO `DeleteContactsRequest` (D-04, hot entity-cache). Empty import → `{is_registered: False}` without caching False.
  - **Skip:** a `not_registered` / non-registered verdict reports not-registered WITHOUT calling Import.
  - **Removed** the sender's own `ResolvePhoneRequest(...)` block AND its now-unused import. `FloodWait`/frozen propagate (not masked); `queue.py` rate intervals untouched (0 references in this file's diff).
- **Task 3 — stale fall-through + confidence gate (SRLD-06/07).**
  - **Part A (D-09):** `_resolve_username` now returns `{"stale_username": True}` on `UsernameNotOccupiedError`/`UsernameInvalidError` (typed catch + string defence-in-depth) instead of caching/returning False; `resolve_contact` routes a stale tier-2 result to the import tier (for a PHONE key) or to `not_registered` (for a `@handle` identity-key contact with no phone).
  - **Part B (D-12):** `_get_cached_contact` runs a single correlated suspect check (`tg_probe_state='suspect' OR tg_confidence IS DISTINCT FROM 'high'`) and applies it to BOTH the per-sender `is_registered=false` early return AND the cross-sender false shortcut — a suspect-source false no longer short-circuits; a clean+high-confidence false still does. The cache is NEVER deleted, only the READ is suppressed.

## Exact contracts (for 17-04 / downstream)

- **Ladder order:** `cache(access_hash)` → `ResolveUsername(captured @username)` → `ImportContacts(tg_status='registered')`. Sender ResolvePhone GONE.
- **Stale-username signal:** `_resolve_username` returns the sentinel dict `{"stale_username": True}` (not `{"is_registered": False}`, not an exception). `resolve_contact` checks `res.get("stale_username")`.
- **ResolvePhoneRequest import:** REMOVED from `app/services/telegram.py` (verified no remaining call; the two surviving textual references are comments). The checker's ResolvePhone is in `checker.py` (untouched).
- **Suspect predicate:** `tg_probe_state = 'suspect' OR tg_confidence IS DISTINCT FROM 'high'` — shared verbatim with 17-02's `_lookup_cache` gate.

## `-k` selectors (this plan)

| Req | File | `-k` selector | Result |
|-----|------|---------------|--------|
| SRLD-03 | test_send.py | `resolve_ladder` | GREEN |
| SRLD-04 | test_send.py | `import_gate` | GREEN |
| SRLD-05 | test_send.py | `lazy_import` | GREEN |
| SRLD-06 | test_send.py | `stale_username_fallthrough` | GREEN |
| SRLD-07 (sender) | test_send.py | `confidence_gated_cache_sender` | GREEN |

## Task Commits

1. **Task 1: `_load_contact_verdict` helper** — `51ffcc5` (feat)
2. **Task 2: rebuild resolve_contact as 3-tier ladder** — `73ceb0c` (feat)
3. **Task 3: stale-username fall-through + confidence-gate false read** — `5557ed4` (fix)

## Files Created/Modified

- `app/services/telegram.py` — `_load_contact_verdict` added; `resolve_contact` rebuilt (cache → ResolveUsername → Import, ResolvePhone + its import removed); `_resolve_username` falls through on stale handle; `_get_cached_contact` confidence-gates both false reads.

## Decisions Made

- **Drop the sender ResolvePhone, not just gate it.** D-01: the sender's own ResolvePhone (and the import that followed it on a phone cache miss) gave the false negatives; the transferable `ResolveUsername(captured)` + a registered-gated `ImportContacts` replace it. A checker's access_hash is per-account (verified) and never reusable on a sender.
- **Stale handle ≠ unregistered.** D-09: a renamed/freed handle means THIS identity is gone, not that the number is dead. Fall through to import (phone key) rather than finalize False. The checker — not the sender — owns negative finalization.
- **Empty registered-gated import does not cache False.** Consistent with D-09: a registered number returning an empty import is most likely privacy/transient, not a true negative.
- **Confidence gate suppresses the READ, never deletes.** D-12: a suspect-source false is not served (forces a live ladder resolve), but the cache row stays — no destructive writes on the read path.

## Deviations from Plan

**None — plan executed exactly as written.** All three tasks committed file-scoped to `app/services/telegram.py` only. No migration was needed (tier-2 reads existing `contacts.tg_username_resolved`; the gate reads existing `contacts.tg_probe_state`/`tg_confidence`), confirmed in the plan. Two pre-existing comments referencing the removed `ResolvePhoneRequest` were updated for accuracy (`_get_cached_contact` conversations note + `send_message` entity-cache note) — wording only, no behavior change.

## Out-of-Scope Failures (NOT regressions)

The end-of-plan full suite ran **848 passed, 1 skipped, 3 failed**. All 3 failures are pre-existing RED owned by a sibling plan / a parallel effort, NOT caused by this plan:

- `tests/test_send.py::test_user_blocked_records_event` (SRLD-08) → **17-04** (block capture; `send_message` has no UserIsBlockedError branch yet).
- `tests/test_restriction_audit.py::test_block_rate_aggregate` (SRLD-08) → **17-04** (`sender_block_rate` helper not built — ImportError).
- `tests/test_warmup_worker.py::test_restricted_sender_excluded` (WARM-14) → **Phase 15 warmup**, an uncommitted parallel working-tree effort in `app/services/warmup.py` (unrelated to the sender).

The suite went from 843 → 848 passed: the 5 SRLD-03..07 sender tests flipped RED → GREEN this plan, no regressions.

## Known Stubs

None.

## Next Phase Readiness

- 17-04 (block capture + docs) is the last wave: add a `UserIsBlockedError` branch in `send_message` returning `code='USER_IS_BLOCKED'` + record a `blocked` restriction event, and add the `app.services.restriction_audit.sender_block_rate` helper. Both have RED tests waiting (`user_blocked`, `block_rate`).
- The sender resolve ladder is structurally complete: a registered RU mobile now resolves via captured-username `ResolveUsername` or `ImportContacts`, never via the sender's own `ResolvePhone`.

## Self-Check: PASSED

- `17-03-SUMMARY.md` — FOUND
- Commits `51ffcc5`, `73ceb0c`, `5557ed4` — all FOUND
- Scope: all 3 task commits touch `app/services/telegram.py` only — `checker.py` untouched; the uncommitted warmup working-tree files (app/main.py, app/services/warmup.py, app/routers/warmup.py, app/models/__init__.py, migrations/040, .planning/config.json) were NOT staged or modified.
- Full suite: 848 passed, 1 skipped, 3 failed (all 3 out-of-scope: 2 owned by 17-04, 1 by parallel Phase 15 warmup).

---
*Phase: 17-sender-side-resolve-ladder-with-username-capture-and-import-fallback*
*Completed: 2026-06-30*
