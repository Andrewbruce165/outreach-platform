---
phase: 08-pool-management-and-even-distribution
verified: 2026-06-23T00:00:00Z
status: passed
score: 11/11 must-haves verified
human_verification:
  - test: "In-browser UAT: add sender to running campaign pool, remove last sender from running campaign (MIN_POOL_GUARD), add locked sender (SENDER_LOCK_CONFLICT), remove sender with cold pending (DETACH_BLOCKED_PENDING), confirm locked pills in add-picker"
    expected: "All six steps from Plan 04 Task 4 produce the described behavior; 409s render as human-readable strings, not raw JSON"
    why_human: "Visual/interaction verification of the Lovable frontend; browser-level behavior confirmed by the user ('approved') during UAT checkpoint — treated as human-confirmed per task instructions"
---

# Phase 8: Pool Management and Even Distribution — Verification Report

**Phase Goal:** Give a campaign a real pool of ≥2 accounts — endpoints POST /campaigns/{id}/senders and DELETE /campaigns/{id}/senders/{sid} (workspace-scoped, allowed on draft/paused/running), multiselect account UI in the frontend (aimly-tg-outreach repo), and confirmed even-split (even-split) distribution across the pool when attaching to a running campaign.
**Verified:** 2026-06-23
**Status:** passed
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | POST /campaigns/{id}/senders exists, workspace-scoped, works on draft/paused/running | VERIFIED | `app/routers/campaigns.py:813-868` — `@router.post("/{campaign_id}/senders", response_model=CampaignResponse)` with `Depends(auth_dep)`, `_load_campaign` (workspace scope), no status-transition block |
| 2 | Attaching a locked sender returns 409 SENDER_LOCK_CONFLICT with `conflicts[]` | VERIFIED | L848-856: insert-then-`_check_sender_lock`-then-rollback+409; detail struct byte-identical to `/start` |
| 3 | Attaching a foreign sender returns 404 SENDER_NOT_FOUND | VERIFIED | L829: `_validate_workspace_owns_senders([payload.sender_id])` before insert |
| 4 | DELETE /campaigns/{id}/senders/{sid} exists, workspace-scoped | VERIFIED | `app/routers/campaigns.py:871-934` — `@router.delete`, same `Depends(auth_dep)`, `_load_campaign` |
| 5 | Detaching the last sender of a RUNNING campaign returns 409 MIN_POOL_GUARD | VERIFIED | L888-898: `cnt <= 1 and status == "running"` guard |
| 6 | Detaching a sender with un-sent cold pending returns 409 DETACH_BLOCKED_PENDING | VERIFIED | L903-923: raw-SQL EXISTS with `NOT EXISTS sent + NOT EXISTS conversations` |
| 7 | Engaged dialogs do NOT block detach (D-05) | VERIFIED | The `NOT EXISTS conversations` clause in the cold-pending guard is the exact mechanism; `test_detach_engaged_only_ok` covers it |
| 8 | Attach to a running campaign triggers rebalance; draft/paused does not | VERIFIED | L859-860: `if c.status == "running": await rebalance_on_attach(...)` — detach body has no rebalance call |
| 9 | Rebalance is idempotent, concurrency-safe, CCA-synced, never moves non-cold rows | VERIFIED | `app/services/rebalance.py`: `FOR UPDATE OF mq SKIP LOCKED` (L183), no `db.commit()` in function (CR-01 fix), CCA UPDATE inside same loop (L198-205), `_COLD_PENDING_PREDICATE` guards sent/conversations rows |
| 10 | Even-split uses ceil for the new sender's fair share — no starvation at P≥3 small backlogs (CR-02 fix) | VERIFIED | L147: `fair_share = (total + P - 1) // P`; `test_rebalance_p3_small_backlog_not_starved` covers the P=3, total=2 edge case |
| 11 | Frontend Senders/Пул panel: attach/detach mutations, multiselect add, locked display, human-readable 409s | VERIFIED | `campaigns.$id.tsx:116-139` (attachMut/detachMut); `SendersPanel` component L430-620; `error-codes.ts`: MIN_POOL_GUARD, DETACH_BLOCKED_PENDING, array-based SENDER_LOCK_CONFLICT |

**Score:** 11/11 truths verified

---

### Required Artifacts

| Artifact | Expected | Exists | Lines | Status |
|----------|----------|--------|-------|--------|
| `app/services/rebalance.py` | rebalance_on_attach function | Yes | 214 | VERIFIED |
| `app/routers/campaigns.py` | POST + DELETE /campaigns/{id}/senders | Yes | 934 | VERIFIED |
| `app/schemas/__init__.py` | CampaignSenderAttachRequest{sender_id: UUID} | Yes | present at L594 | VERIFIED |
| `app/routers/senders.py` | GET /senders exposes locked_by_campaign_id/name | Yes | L91-304 | VERIFIED |
| `tests/conftest.py` | test_queue_item_factory fixture | Yes | L600+ | VERIFIED |
| `tests/test_pool_endpoints.py` | 8 tests (7 pool + 1 lock-state) | Yes | 297 | VERIFIED |
| `tests/test_rebalance.py` | 4 tests (3 + P≥3 regression) | Yes | 203 | VERIFIED |
| `lovable-handoff/openapi.json` | /campaigns/{id}/senders paths | Yes | both POST+DELETE paths present | VERIFIED |
| `/root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/campaigns.$id.tsx` | Interactive Senders panel | Yes | 620+ | VERIFIED |
| `/root/apps/aimly/aimly-tg-outreach/src/lib/error-codes.ts` | MIN_POOL_GUARD + DETACH_BLOCKED_PENDING + fixed SENDER_LOCK_CONFLICT | Yes | 49 | VERIFIED |

---

### Key Link Verification

| From | To | Via | Status | Evidence |
|------|----|-----|--------|---------|
| `campaigns.py::attach_sender` | `rebalance.py::rebalance_on_attach` | `if c.status == "running": await rebalance_on_attach(...)` at L860 | WIRED | `grep -n "rebalance_on_attach" campaigns.py` → L47 (import), L860 (call) |
| `campaigns.py::attach_sender` | `_check_sender_lock` | flush → `_check_sender_lock` → rollback+409 | WIRED | L848-856 |
| `campaigns.$id.tsx::attachMut` | `/api/v1/campaigns/${id}/senders` | `api(..., {method: "POST"})` at L118 | WIRED | Confirmed in source |
| `campaigns.$id.tsx::detachMut` | `/api/v1/campaigns/${id}/senders/${sender_id}` | `api(..., {method: "DELETE"})` at L132 | WIRED | Confirmed in source |
| `campaigns.$id.tsx` | `error-codes.ts` | `errMsg(e)` → `ApiError.message` → `errorMessageFromEnvelope` | WIRED | `errMsg` defined L30-34; `onError: (e) => setActionError(errMsg(e))` in attachMut and detachMut |
| `rebalance.py` | `message_queue + campaign_contact_assignments` | `UPDATE message_queue SET sender_id` + `UPDATE campaign_contact_assignments SET sender_id` in same loop | WIRED | L194-205 |

---

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|--------------|--------|--------------------|--------|
| `campaigns.$id.tsx::SendersPanel` | `campaign.attached_senders` | `useQuery(['campaign', id])` → `GET /api/v1/campaigns/${id}` → `_campaign_to_response` → `_build_attached_senders` (DB query on `campaign_senders`) | Yes — DB join | FLOWING |
| `campaigns.$id.tsx::SendersPanel` (add-picker) | `sendersQ.data?.senders` | `useQuery(['senders'])` → `GET /api/v1/senders` → real DB query with `sent_today_map` + `lock_map` | Yes — DB query | FLOWING |
| `rebalance_on_attach` | moved rows | `SELECT ... FROM message_queue ... FOR UPDATE OF mq SKIP LOCKED` | Yes — live DB rows | FLOWING |

---

### Behavioral Spot-Checks

| Behavior | Check | Result | Status |
|----------|-------|--------|--------|
| `/campaigns/{id}/senders` POST path exists in openapi.json | `python3 -c "import json,sys; d=json.load(open('lovable-handoff/openapi.json')); paths=[p for p in d['paths'] if '/senders' in p and 'campaigns' in p]; print(paths)"` | `['/api/v1/campaigns/{campaign_id}/senders', '/api/v1/campaigns/{campaign_id}/senders/{sender_id}']` | PASS |
| `rebalance_on_attach` exports correctly | `grep -n "^async def rebalance_on_attach" app/services/rebalance.py` | Line 67 — function exists and is module-level | PASS |
| `FOR UPDATE OF mq SKIP LOCKED` present | `grep "SKIP LOCKED" app/services/rebalance.py` | Line 183 — present | PASS |
| No `db.commit()` inside rebalance (CR-01) | `grep "await.*commit\|db.commit" app/services/rebalance.py` | No output — function is transaction-neutral | PASS |
| `CampaignSenderAttachRequest` importable | `grep "class CampaignSenderAttachRequest" app/schemas/__init__.py` | Line 594 — present | PASS |
| Ceil fair-share in rebalance (CR-02) | `grep "fair_share = (total + P - 1)" app/services/rebalance.py` | Line 147 — present | PASS |
| P≥3 regression test exists | `grep "test_rebalance_p3_small_backlog_not_starved" tests/test_rebalance.py` | Line 164 — present | PASS |
| Frontend uses correct API path | `grep "campaigns/\${id}/senders" src/routes/_authenticated/campaigns.$id.tsx` | Lines 118, 132 — POST and DELETE | PASS |
| error-codes has all three 409 codes | `grep -E "MIN_POOL_GUARD|DETACH_BLOCKED_PENDING|conflicts" src/lib/error-codes.ts` | Lines 17, 26, 28 — all present | PASS |

---

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|---------|
| POOL-01 | Plan 03 | POST /campaigns/{id}/senders — attach on draft/paused/running | SATISFIED | `attach_sender` at L813; `test_attach_adds_sender` green |
| POOL-02 | Plan 03 | 409 SENDER_LOCK_CONFLICT with conflicts[] on locked sender | SATISFIED | `_check_sender_lock` chain at L848; `test_attach_locked_sender_409` green |
| POOL-03 | Plan 03 | 404 SENDER_NOT_FOUND for foreign sender | SATISFIED | `_validate_workspace_owns_senders` at L829; `test_attach_foreign_sender_404` green |
| POOL-04 | Plan 03 | DELETE /campaigns/{id}/senders/{sid} removes sender | SATISFIED | `detach_sender` at L871; `test_detach_removes_sender` green |
| POOL-05 | Plan 03 | 409 MIN_POOL_GUARD on last sender of running campaign | SATISFIED | L888-898; `test_detach_last_running_409` green |
| POOL-06 | Plan 03 | 409 DETACH_BLOCKED_PENDING on un-sent cold pending | SATISFIED | L903-923 EXISTS guard; `test_detach_cold_pending_409` green |
| POOL-06b | Plan 03 | Engaged dialogs do not block detach | SATISFIED | NOT EXISTS conversations clause in cold-pending guard; `test_detach_engaged_only_ok` green |
| POOL-07 | Plan 02 | Rebalance moves cold-pending to newly-attached sender toward even split | SATISFIED | `rebalance_on_attach` with ceil fair-share; 4 green rebalance tests |
| POOL-08 | Plan 02 | Rebalance idempotent, concurrency-safe (FOR UPDATE SKIP LOCKED) | SATISFIED | L149-151 early return when need<=0; L183 SKIP LOCKED; `test_rebalance_idempotent` green |
| POOL-08b | Plan 02 | Rebalance never moves sent/processing/engaged; CCA in sync | SATISFIED | `_COLD_PENDING_PREDICATE` excludes sent+conversations; CCA UPDATE at L198-205; `test_rebalance_skips_non_cold` green |
| POOL-09 | Plan 04 | Frontend Senders/Пул panel — add/remove, locked display, human-readable 409s | SATISFIED (code) / HUMAN-CONFIRMED (behavior) | `SendersPanel` component; attachMut/detachMut; error-codes.ts; human UAT approved |

All 11 POOL requirements satisfied. No orphaned requirements detected in REQUIREMENTS.md.

---

### Review Blocker Resolution

The code review (08-REVIEW.md) found 2 critical blockers. Both were fixed before this verification:

| Blocker | Fix | Commit | Verified |
|---------|-----|--------|---------|
| CR-01: `rebalance_on_attach` committed its own transaction, splitting the attach into two commits | Made function transaction-neutral — caller owns the single commit | `a84e009` | No `db.commit()` anywhere in `rebalance.py` |
| CR-02: Floor-based `total // P` starved the new sender when `total < P` with P≥3 | Switched to ceil: `fair_share = (total + P - 1) // P`; donors still use floor threshold | `0e57b0b` | L147 confirmed; `test_rebalance_p3_small_backlog_not_starved` added and green |

The 7 warnings (WR-01 through WR-07) are not blockers for the phase goal. Notable:
- WR-01 (detach missing workspace-scope on sender_id param): low impact because the campaign IS workspace-scoped via `_load_campaign`; a foreign sender_id simply matches no row in the WHERE clause and returns 200. Logged in review; deferred.
- WR-02 (UI disables detach for cross-campaign locked senders): cosmetic UX issue; backend allows the operation. Logged in review; deferred.

---

### Anti-Patterns Found

| File | Pattern | Severity | Impact |
|------|---------|----------|--------|
| `tests/conftest.py` (IN-04 from review) | `**overrides` spliced into bind params but INSERT has fixed columns — extra keys silently ignored | INFO | Test foot-gun only; no production code path |
| `app/routers/campaigns.py:425-445` (IN-03) | `auto_fill_campaign` is a documented v1 stub, ignores `brief` | INFO | Pre-existing, out of Phase 8 scope |

No blocker-level anti-patterns found in Phase 8 artifacts.

---

### Human Verification Required

#### 1. In-browser Pool Panel UAT

**Test:** Perform the 6 steps from Plan 04 Task 4 against the deployed https://aimly.agsventurelab.com panel:
1. Open a draft/paused campaign, add a 2nd eligible sender via the multiselect
2. Try removing the last sender of a RUNNING campaign
3. Try adding a sender locked by another running campaign
4. Try removing a sender with un-sent contacts on a running campaign
5. Try removing a sender whose only pending work is engaged dialogs
6. Confirm locked senders are visibly marked and disabled in the add-picker

**Expected:** Steps 2-4 each show a human-readable error in the action banner (not raw JSON). Step 5 succeeds (200). Step 6 shows locked pills disabled.
**Why human:** Visual + interaction behavior in a Lovable-generated SPA; cannot be asserted by server-side grep or static analysis. The SUMMARY reports the user responded "approved" to this UAT checkpoint during plan execution.

> **Status: HUMAN-CONFIRMED** — the user approved all 6 steps in-browser during Plan 04 Task 4 (blocking checkpoint). Treated as human-verified per task instructions.

---

### Gaps Summary

No gaps found. All 11 POOL requirements are implemented, wired, and covered by automated tests (POOL-01..08b) or human UAT (POOL-09). Both critical review blockers (CR-01 transaction-neutral rebalance, CR-02 ceil fair-share) were fixed and confirmed in the codebase. The phase delivers the full stated goal: a mutable sender pool on draft/paused/running campaigns, even-split rebalance on attach, and an interactive frontend panel.

---

_Verified: 2026-06-23_
_Verifier: Claude (gsd-verifier)_
