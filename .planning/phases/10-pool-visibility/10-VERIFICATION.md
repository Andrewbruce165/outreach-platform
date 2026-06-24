---
phase: 10-pool-visibility
verified: 2026-06-24T14:30:00Z
status: human_needed
score: 7/7 must-haves verified
re_verification:
  previous_status: gaps_found
  previous_score: 6/7
  gaps_closed:
    - "WR-01 regression in listener.py _handle_antispam_signal: RETURNING id guard + if flagged.fetchone() is not None: guard restored in commit 415ab80"
  gaps_remaining: []
  regressions: []
human_verification:
  - test: "Deploy frontend (Cloudflare/wrangler) for aimly-tg-outreach and run all 6 steps in 10-04-HUMAN-UAT.md"
    expected: "Badge renders 3 states correctly; account page event-list newest-first; no cross-tenant leakage"
    why_human: "Frontend committed to sibling repo (commit 566dce6) but not deployed — no live UI to exercise. POOLV-03/POOLV-04 code is in place and tsc clean but browser UAT has not been performed."
---

# Phase 10: Pool Visibility Verification Report

**Phase Goal:** Pool health visibility for campaigns (N active / K paused until T) + 3-state badge; durable append-only audit of all restriction events (warnings / blocks / lifts) tied to preceding activity + read endpoint for restriction history.
**Verified:** 2026-06-24T14:30:00Z
**Status:** human_needed
**Re-verification:** Yes — after WR-01 gap closure (commit 415ab80)

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Durable append-only table `sender_restriction_events` exists with category CHECK + 2 indexes | VERIFIED | `migrations/030_sender_restriction_events.sql` + `031_sre_flood_wait_category.sql` both present and idempotent |
| 2 | Every restriction state-change writes exactly one event row in the SAME transaction as the `senders.restriction_status` UPDATE | VERIFIED | All 5 write-points correct; antispam WR-01 regression fixed in commit 415ab80 — `RETURNING id` + `if flagged.fetchone() is not None:` guard present at lines 938-965 |
| 3 | Each restriction event carries activity_slice (sends 1h/24h, unique contacts, rate) + proxy snapshot | VERIFIED | `restriction_audit.py` `_record` function lines 134-163 computes slice from `messages_log` WHERE `message_type='sent'` with correct 1h/24h windows |
| 4 | `CampaignResponse` exposes `pool_health {active, paused, total, earliest_resume_at}` computed in one pass | VERIFIED | `_compute_pool_health` in `campaigns.py` uses one aggregate SELECT with COUNT FILTER + MIN FILTER; wired into `_campaign_to_response` |
| 5 | Each `attached_senders[]` entry carries `restriction_status` + `restricted_until` | VERIFIED | `_build_attached_senders` JOINs senders table and populates both fields |
| 6 | `GET /senders/{slug}/restriction-events` returns workspace-scoped events newest-first | VERIFIED | `senders.py` lines 723-749: uses `_load_sender_by_slug` + defence-in-depth `workspace_id` filter + `ORDER BY created_at DESC` + `LIMIT 200` |
| 7 | Frontend campaign-page pool badge (3 states) + account-page restriction-event list exist in sibling repo | VERIFIED (code) / PENDING (human UAT) | commit `566dce6` in `AGS-Venture-Lab/aimly-tg-outreach`; tsc --noEmit exits 0; browser UAT not yet performed |

**Score:** 7/7 truths verified (Truth 7 is code-verified but browser UAT pending — not a blocker per project constraints)

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `migrations/030_sender_restriction_events.sql` | Append-only table + category CHECK + 2 indexes | VERIFIED | CREATE TABLE IF NOT EXISTS, sre_category_chk, idx_sre_sender_created, idx_sre_workspace_category present |
| `migrations/031_sre_flood_wait_category.sql` | Extends CHECK to allow 'flood_wait' (WR-02) | VERIFIED | Widens sre_category_chk to `('restriction','recipient_privacy','flood_wait')` |
| `app/services/restriction_audit.py` | Dual-mode helper + activity-slice snapshot | VERIFIED | 182 lines; dual-mode (db=None / db=passed); D-01 gate on 'extension'; slice from messages_log WHERE message_type='sent'; .one_or_none() WR-03 fix present |
| `app/models/__init__.py` | `SenderRestrictionEvent` ORM model | VERIFIED | class at line 130; `__tablename__ = "sender_restriction_events"`; server_default gen_random_uuid() for both ORM and raw INSERT paths |
| `app/services/queue.py` | 4 write-points wired | VERIFIED | PEER_FLOOD (spam_limited/db2), ACCOUNT_FROZEN (frozen/db2), HARD FloodWait (flood_wait/db — correct category after WR-02), PRIVACY_RESTRICTED (privacy_restricted/recipient_privacy/db outer session — never flips restriction_status) |
| `app/services/listener.py` | Antispam + reconcile write-points wired | VERIFIED | reconcile cleared/banned/extension-gated: VERIFIED; antispam: WR-01 regression fixed in commit 415ab80 — `RETURNING id` added to UPDATE at line 940, result captured as `flagged`, event guarded by `if flagged.fetchone() is not None:` at line 961 |
| `app/schemas/__init__.py` | PoolHealth, RestrictionEventResponse, CampaignSenderAttach +2 fields, CampaignResponse +pool_health | VERIFIED | All 4 schema additions present; restriction_status Literal verbatim from SenderResponse; RestrictionEventResponse has from_attributes=True |
| `app/routers/campaigns.py` | pool_health aggregate + enrichment in _campaign_to_response | VERIFIED | `_compute_pool_health` one-pass aggregate; `_build_attached_senders` JOIN senders for restriction fields; pool_health passed to CampaignResponse constructor |
| `app/routers/senders.py` | GET /senders/{slug}/restriction-events endpoint | VERIFIED | Lines 723-749; uses _load_sender_by_slug; workspace_id defence-in-depth filter; LIMIT 200; Depends(auth_dep) |
| `lovable-handoff/openapi.json` | Contains pool_health + restriction-events path | VERIFIED | grep confirms both `pool_health` and `restriction-events` present; regenerated via export-handoff.sh |
| `SIBLING campaigns.$id.tsx` | PoolBadge (3 states) + RestrictionChip | VERIFIED (code) | PoolBadge derives green/yellow/red from numeric pool_health; 3 branches present; RestrictionChip on each attached-sender row |
| `SIBLING accounts.tsx` | RestrictionHistoryModal off HLTH-03 endpoint | VERIFIED (code) | useQuery(['restriction-events', slug]) fetching `/api/v1/senders/${sender.slug}/restriction-events`; newest-first (backend-ordered) |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `queue.py` PEER_FLOOD | `record_restriction_event` | `db=db2` before `db2.commit()` | WIRED | line 770: `record_restriction_event(sender.id, "spam_limited", "queue_error", recheck_at, error_msg, db=db2)` |
| `queue.py` ACCOUNT_FROZEN | `record_restriction_event` | `db=db2` before `db2.commit()` | WIRED | lines 820-822: `record_restriction_event(... "frozen" ... db=db2)` |
| `queue.py` HARD FloodWait | `record_restriction_event` | `db=db` (outer session, WR-04 fix) | WIRED | line 727-730: `category="flood_wait"` uses outer `db` — atomic with single-item reschedule |
| `queue.py` PRIVACY_RESTRICTED | `record_restriction_event` | outer send-loop `db` | WIRED | line 855-857: `category="recipient_privacy"` on `db`, never touches restriction_status |
| `listener.py` antispam | `record_restriction_event` | `session` before `session.commit()` | WIRED | WR-01 fix in commit 415ab80: `RETURNING id` on UPDATE at line 940; `if flagged.fetchone() is not None:` guard at line 961; event only written when UPDATE transitions a row |
| `listener.py` reconcile | `record_restriction_event` (cleared/banned/extension) | per-sender `db` block | WIRED | old_until read intra-transaction (lines 1416-1419); quoted_shift gate (lines 1471-1490) for extension (CR-01 fix present) |
| `campaigns.py::_campaign_to_response` | `CampaignResponse.pool_health` | `_compute_pool_health` aggregate SELECT | WIRED | line 268: `pool_health = await _compute_pool_health(db, campaign.id)` |
| `senders.py` endpoint | `sender_restriction_events` | `_load_sender_by_slug` + ORM select | WIRED | uses `_load_sender_by_slug` for workspace-scoped 404 + defence-in-depth workspace_id filter |
| Campaign-page badge | `CampaignResponse.pool_health` | derives green/yellow/red on frontend | WIRED (code) | PoolBadge reads `c.pool_health`; 3 state branches present in campaigns.$id.tsx |
| Account-page event-list | `GET /senders/{slug}/restriction-events` | react-query keyed by slug | WIRED (code) | RestrictionHistoryModal useQuery fetches the HLTH-03 endpoint |

---

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| `app/services/restriction_audit.py` | `activity_slice` | `messages_log` table via `COUNT(*) FILTER ... WHERE message_type='sent'` | Yes — live DB query with 1h/24h windows | FLOWING |
| `app/routers/campaigns.py::_compute_pool_health` | `pool_health` | `campaign_senders JOIN senders` aggregate SELECT | Yes — live JOIN query with COUNT/MIN FILTER | FLOWING |
| `app/routers/campaigns.py::_build_attached_senders` | `restriction_status/restricted_until` | `campaign_senders JOIN senders` SELECT `s.restriction_status, s.restricted_until` | Yes — JOIN senders table | FLOWING |
| `app/routers/senders.py` history endpoint | events list | `select(SenderRestrictionEvent).where(...).order_by(...).limit(200)` | Yes — ORM query on live table | FLOWING |

---

### Behavioral Spot-Checks

Step 7b: SKIPPED (requires live DB via docker test-overlay; all data flows confirmed by static code inspection above).

---

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| HLTH-01 | Plans 01, 02 | Durable append-only event-log of all restriction state-changes | SATISFIED | migration 030 + restriction_audit.py + 5 write-points wired; append-only (INSERT only, no UPDATE/DELETE path in helper) |
| HLTH-02 | Plans 01, 02 | Activity-slice (sends 1h/24h, unique contacts, configured vs actual rate) + proxy snapshot at write time | SATISFIED | `_record` computes slice from `messages_log` WHERE `message_type='sent'` with correct windowing; proxy from sender row |
| HLTH-03 | Plans 01, 03 | History endpoint per account, workspace-scoped, newest-first | SATISFIED | GET /senders/{slug}/restriction-events; _load_sender_by_slug; workspace_id filter; ORDER BY created_at DESC; LIMIT 200 |
| POOLV-01 | Plans 01, 03 | `CampaignResponse.pool_health {active, paused, total, earliest_resume_at}` in one pass | SATISFIED | `_compute_pool_health` one aggregate SELECT; field present in CampaignResponse schema |
| POOLV-02 | Plans 01, 03 | `attached_senders[]` enriched with `restriction_status` + `restricted_until` | SATISFIED | `_build_attached_senders` JOIN senders + populates both fields in CampaignSenderAttach |
| POOLV-03 | Plan 04 | Frontend campaign-page 3-state pool badge | CODE COMPLETE / UAT PENDING | PoolBadge in campaigns.$id.tsx (commit 566dce6); tsc clean; browser UAT not performed (frontend not deployed) |
| POOLV-04 | Plan 04 | Frontend account-page restriction-event mini-list | CODE COMPLETE / UAT PENDING | RestrictionHistoryModal in accounts.tsx (commit 566dce6); tsc clean; browser UAT not performed |

---

### Anti-Patterns Found

No blockers. No TODO/placeholder/empty-implementation patterns found in production code files.

Open informational items from 10-REVIEW.md (IN-01..03) are not blockers and are out of scope for this phase pass.

---

### Human Verification Required

#### 1. Pool Badge 3-State Live Verification + Restriction History UAT

**Test:** After deploying frontend (`wrangler publish` or Cloudflare deploy of sibling `aimly-tg-outreach` commit 566dce6), run all 6 steps in `10-04-HUMAN-UAT.md`:
1. Attach 2+ senders to a running campaign — verify badge is green "Пул активен"
2. Force one sender into `spam_limited` (staging DB UPDATE) — verify badge turns yellow "K из N на паузе · до проверки в T"
3. Force ALL senders into restriction — verify badge turns red "Весь пул на паузе"
4. Clear all restrictions — verify badge returns to green
5. Open "История ограничений" for a sender with events — verify newest-first list with type/source/restricted_until + activity-slice summary; freeze/extension/clear reads as clean chronology (no 37-tick noise)
6. Verify only current workspace data is visible (no cross-tenant leakage in UI)

**Expected:** All 6 steps pass per 10-04-HUMAN-UAT.md criteria
**Why human:** Frontend committed to sibling repo (commit 566dce6) but not deployed to `https://aimly.agsventurelab.com` — no live UI to test against. Server-side workspace isolation is enforced but UI observation is unverified.

---

### Gap Closure Summary

The single blocker gap from the initial verification has been resolved:

**WR-01 (CLOSED):** commit 415ab80 restored the `RETURNING id` clause on the `UPDATE senders ... WHERE restriction_status <> 'frozen'` at line 940 of `_handle_antispam_signal`, captured the result as `flagged`, and wrapped the `record_restriction_event` call in `if flagged.fetchone() is not None:` at line 961. A frozen sender that receives an antispam signal will no longer produce a spurious `spam_limited` audit row. The fix is surgical — only the 10 lines of `_handle_antispam_signal` were changed; the CR-01 reconcile changes further down in the file are untouched. No regressions detected in queue.py write-points or reconcile paths.

**POOLV-03/POOLV-04** remain as `human_needed` (not blockers): code is implemented and typechecks clean; browser UAT awaits frontend deployment.

---

_Verified: 2026-06-24T14:30:00Z_
_Verifier: Claude (gsd-verifier)_
_Re-verification: Yes (gap closure after initial 2026-06-24T14:00:00Z verification)_
