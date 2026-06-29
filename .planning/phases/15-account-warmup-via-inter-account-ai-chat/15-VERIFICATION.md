---
phase: 15-account-warmup-via-inter-account-ai-chat
verified: 2026-06-29T14:30:00Z
status: passed
score: 15/15 requirements verified
re_verification: false
human_verification:
  - test: "Open warmup tab on aimly.agsventurelab.com — toggle master warmup on/off"
    expected: "Master toggle persists; per-account rows show level/sent_today and restriction reason where relevant"
    why_human: "Frontend tab lives in sibling repo (aimly-tg-outreach), generated from openapi.json, requires manual UAT per 15-VALIDATION.md"
---

# Phase 15: Account Warmup via Inter-Account AI Chat — Verification Report

**Phase Goal:** Make per-account warmup safe and multi-tenant — (1) deterministic internal-traffic isolation so the listener never AI-replies to or logs warmup traffic between the workspace's own accounts, (2) a per-workspace master enable flag + per-workspace warmup content with code-default fallback, (3) warmup engine skips restricted/frozen senders and stays off the protected message_queue path, (4) all warmup API endpoints workspace-scoped via auth_dep/AuthCtx.

**Verified:** 2026-06-29T14:30:00Z
**Status:** PASSED
**Re-verification:** No — initial verification

---

## Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Internal-sender detection by telegram_id fires BEFORE AI dispatch and BEFORE any DB write | VERIFIED | `listener.py:720-734` — short-circuit placed after skip-self, before bot/antispam/AI branches |
| 2 | Internal detection is NOT phone-dependent, NOT pool-membership-dependent | VERIFIED | `listener.py:615-663` — `_get_workspace_sender_tg_ids` queries `senders` directly, no `warmup_pool` join, no phone filter |
| 3 | Outgoing handler has a symmetric short-circuit | VERIFIED | `listener.py:1236-1249` — drops before conversation lookup |
| 4 | Disabled/absent warmup_settings workspace → zero pool members → zero sessions | VERIFIED | `warmup.py:186-202` — `COALESCE(ws.enabled, false) = true` gate via LEFT JOIN |
| 5 | Empty settings resolve to 24 RU WARMUP_TOPICS + WARMUP_SYSTEM_PROMPT | VERIFIED | `warmup.py:244-259` — `_get_warmup_content` COALESCE fallback |
| 6 | spam_limited/frozen/future-restricted senders excluded from pool selection | VERIFIED | `warmup.py:200-201` — `restriction_status='none' AND (restricted_until IS NULL OR restricted_until<=NOW())` |
| 7 | Mid-session restriction also stops warmup | VERIFIED | `warmup.py:308-358` — `_not_restricted` helper in `_process_session.is_eligible` |
| 8 | All /api/v1/warmup endpoints workspace-scoped via auth_dep | VERIFIED | `warmup.py:85,173,208,244,277,334,395,438,531,555` — every endpoint has `ctx: AuthCtx = Depends(auth_dep)` |
| 9 | /pool no longer references dropped senders.is_active column | VERIFIED | `warmup.py:94-121` — only `wp.is_active AS warmup_active` (warmup_pool column), no `s.is_active` |
| 10 | GET/PUT /settings present, workspace-scoped | VERIFIED | `warmup.py:528-596` — both endpoints with `auth_dep`, idempotent upsert |
| 11 | Warmup stays on direct Telethon, never touches message_queue | VERIFIED | `warmup.py:477,640-676` — `_send_via_telethon` uses `client.send_message`, no `message_queue` reference |
| 12 | Onboarding never auto-enrolls in warmup_pool | VERIFIED | `grep warmup_pool app/routers/onboarding.py` → 0 matches |
| 13 | Router mounted in app/main.py | VERIFIED | `main.py:192` — `app.include_router(warmup.router)` |
| 14 | Migration 038 is idempotent, enabled DEFAULT FALSE, no live-workspace seed | VERIFIED | `migrations/038_warmup_settings.sql` — `CREATE TABLE IF NOT EXISTS`, `enabled BOOLEAN NOT NULL DEFAULT FALSE`, no INSERT |
| 15 | SpamBot classify_spambot_text block intact (parallel-agent regression guard) | VERIFIED | `listener.py:1063-1070` — `classify_spambot_text` import and verdict check untouched |

**Score: 15/15 truths verified**

---

## Required Artifacts

| Artifact | Description | Status | Details |
|----------|-------------|--------|---------|
| `app/services/listener.py` | Deterministic internal short-circuit (WARM-01/02/04) | VERIFIED | `_get_workspace_sender_tg_ids` at line 615; wired in both handlers at lines 727 and 1242 |
| `app/services/warmup.py` | Enabled-gate + content resolver + restriction-skip (WARM-06/10/14) | VERIFIED | `_get_active_pool` has LEFT JOIN warmup_settings; `_get_warmup_content` method; restriction clause in both pool select and `_process_session` |
| `app/routers/warmup.py` | Workspace-scoped router (WARM-05/07/08/09/11) | VERIFIED | All 8 endpoints + GET/PUT /settings, all on `auth_dep`, no `verify_api_key` |
| `app/main.py` | Router mounted | VERIFIED | Line 192: `app.include_router(warmup.router)` |
| `migrations/038_warmup_settings.sql` | Per-workspace settings table (WARM-06/10) | VERIFIED | Idempotent, DEFAULT FALSE, no seed |
| `app/models/__init__.py` | WarmupSettings ORM | VERIFIED | Class at line 398, `__tablename__ = "warmup_settings"` |
| `tests/test_warmup_isolation.py` | WARM-01/02/04 regression guards | VERIFIED | 3 tests — all PASSING |
| `tests/test_warmup_worker.py` | WARM-06/10/14 regression guards | VERIFIED | 3 tests — all PASSING |
| `tests/test_warmup_router.py` | WARM-05 scoping + /settings | VERIFIED | 4 tests (2 WARM-05 + 2 settings) — all PASSING |

---

## Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `handle_incoming_message` | `_get_workspace_sender_tg_ids` | call at line 727 | WIRED | Internal drop fires BEFORE antispam, bots, AI |
| `handle_outgoing_message` | `_get_workspace_sender_tg_ids` | call at line 1242 | WIRED | Internal drop fires BEFORE conversation lookup |
| `WarmupWorker._get_active_pool` | `warmup_settings` | LEFT JOIN + COALESCE | WIRED | Disabled/absent workspace → no pool members |
| `WarmupWorker._process_session` | `senders.restriction_status` | fetches + `_not_restricted` | WIRED | Mid-session restriction stops the session |
| `WarmupWorker._get_warmup_content` | `warmup_settings` | SELECT topics, system_prompt | WIRED | Falls back to WARMUP_TOPICS/WARMUP_SYSTEM_PROMPT on miss |
| `warmup.router.list_pool` | `senders.restriction_status` | SQL column + `_derive_warmup_reason` | WIRED | D-11 restriction reason in response |
| `warmup.router` | `auth_dep` | `Depends(auth_dep)` on every endpoint | WIRED | All 10 route handlers carry AuthCtx |
| `app.main` | `warmup.router` | `app.include_router(warmup.router)` | WIRED | Router accessible under /api/v1/warmup |

---

## Behavioral Spot-Checks

| Behavior | Command / Evidence | Result | Status |
|----------|--------------------|--------|--------|
| All 10 warmup tests pass | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_warmup_isolation.py tests/test_warmup_router.py tests/test_warmup_worker.py -v` | 10 passed, 1 warning | PASS |
| Internal short-circuit in both handlers | `grep -n "_get_workspace_sender_tg_ids" app/services/listener.py` | Lines 615, 642, 663, 671, 727, 1242 | PASS |
| No `verify_api_key` in warmup router | `grep "verify_api_key" app/routers/warmup.py` | 0 occurrences in code (only in docstring comment) | PASS |
| No `s.is_active` column reference in warmup router | `grep "s\.is_active" app/routers/warmup.py` | 0 occurrences | PASS |
| No `warmup_pool` in onboarding router | `grep "warmup_pool" app/routers/onboarding.py` | 0 occurrences | PASS |
| SpamBot classify block intact | `grep -n "classify_spambot_text" app/services/listener.py` | Lines 1063-1065 | PASS |

---

## Requirements Coverage

| Requirement | Description | Status | Evidence |
|-------------|-------------|--------|----------|
| WARM-01 | Internal-detection by telegram_id ∈ workspace senders; not phone-/pool-dependent | SATISFIED | `listener.py:615-663, 727-734` — `_get_workspace_sender_tg_ids` queries senders directly; test `test_internal_detected_by_workspace_telegram_id` GREEN |
| WARM-02 | Internal inbound → no conversations/messages row, no AI | SATISFIED | `listener.py:720-734` — returns before any DB write or `schedule_ai_response`; test `test_internal_inbound_no_dbwrite_no_ai` GREEN |
| WARM-03 | Warmup uses direct Telethon, not message_queue | SATISFIED | `warmup.py:640-676` — `_send_via_telethon` uses `client.send_message`; `queue.py` diff-clean |
| WARM-04 | Source-introspection guard: short-circuit in both handlers | SATISFIED | `test_shortcircuit_wired` checks `_get_workspace_sender_tg_ids` in both handler sources — GREEN |
| WARM-05 | All /api/v1/warmup under AuthDep + workspace scope | SATISFIED | Every endpoint in `warmup.py` carries `ctx: AuthCtx = Depends(auth_dep)` + `WHERE workspace_id = :wid`; tests GREEN |
| WARM-06 | warmup_enabled per-workspace; worker honors flag | SATISFIED | `warmup.py:193-199` — LEFT JOIN warmup_settings + COALESCE(ws.enabled,false)=true; `test_disabled_workspace_skipped` GREEN |
| WARM-07 | UI master toggle + per-account enroll/toggle endpoints | SATISFIED | GET/PUT /settings for master toggle; POST/DELETE/PATCH /pool/{id}/toggle for per-account. UI is human-verified (manual-only checklist) |
| WARM-08 | Schedule 09-20 MSK without UI config | SATISFIED | `warmup.py:144-147` — `_is_working_hours` unchanged, 9<=hour<20 MSK |
| WARM-09 | Auto intensity by days; UI read-only level/progress | SATISFIED | `LEVEL_CONFIG` unchanged; `level` exposed in /pool and /stats responses; no UI ручного управления |
| WARM-10 | Per-workspace content with default=24 RU topics + prompt | SATISFIED | `warmup.py:244-259` — `_get_warmup_content` with COALESCE; `test_content_defaults_when_empty` GREEN; `warmup_settings` upsert in router |
| WARM-11 | Enriched per-account status with restriction_status + warmup_reason | SATISFIED | `warmup.py:112-140` — restriction_status, restricted_until, warmup_reason in /pool response; `test_response_shapes_preserved` GREEN |
| WARM-12 | Combine warmup with active campaign allowed | SATISFIED | No auto-pause logic added anywhere; D-12 invariant confirmed in Plan 03 |
| WARM-13 | New accounts not auto-enrolled in warmup pool on onboarding | SATISFIED | `grep warmup_pool app/routers/onboarding.py` = 0; D-13 invariant confirmed in Plan 03 |
| WARM-14 | Pool selection skips restriction_status != 'none' / future restricted_until | SATISFIED | `warmup.py:200-201` — explicit SQL clause; `test_restricted_sender_excluded` GREEN |
| WARM-15 | Old telegram-api warmup root cause documented (isolation analysis) | SATISFIED | Full diagnosis in `.planning/debug/dashboard-analytics-warmup-pollution.md` + `15-RESEARCH.md §State of the Art` + Plan 02 summary; phone-only leak closed by D-01 implementation |

---

## Anti-Patterns Found

| File | Pattern | Severity | Assessment |
|------|---------|----------|------------|
| `app/services/listener.py:571-603` | Legacy `_refresh_warmup_cache` / `_get_warmup_phones` / `_get_warmup_telegram_ids` methods retained | INFO | Not a stub — these are the OLD warmup-pool-based cache methods left in place intentionally (Plan 02 decision: "no longer the primary isolation signal, harmless and unreferenced by the new path"). The new `_get_workspace_sender_tg_ids` is the single source of truth. No code path leads from the new short-circuit to these old methods. |
| `warmup.py:477` | Writes warmup_message row before Telethon send | INFO | Intentional design ("пишем в БД ДО отправки") — not a stub. Provides a pre-send audit trail consistent with the existing pattern. |

No blockers. No stub implementations. No hardcoded empty returns on user-visible paths.

---

## Human Verification Required

### 1. Warmup UI Tab (WARM-07 / WARM-11)

**Test:** After API deploy, open the warmup tab on `aimly.agsventurelab.com`
**Expected:** Master toggle visible and persists on/off; per-account rows show level, sent_today, restriction reason (if any); per-account enroll/toggle buttons work
**Why human:** Frontend tab lives in the sibling repo `aimly-tg-outreach`, generated from openapi.json via Lovable. This is explicitly marked as manual-only in `15-VALIDATION.md`.

---

## Gaps Summary

No gaps found. All 15 WARM-XX requirements are SATISFIED. All 10 automated regression tests pass (warmup isolation trio + worker trio + router quartet). The phase goal — deterministic internal-traffic isolation, per-workspace master flag + content with fallback, restriction-skipping pool selection, and fully workspace-scoped API — is fully achieved in the codebase.

The only open item is human UAT of the frontend warmup tab, which is explicitly out of scope for code verification per `15-VALIDATION.md`.

---

## Summary Statistics

- Plans completed: 4 (01 foundation+RED scaffold, 02 isolation, 03 engine, 04 API)
- Tests added: 10 (3 isolation + 3 worker + 4 router)
- Tests passing: 10/10
- Files modified: listener.py, warmup.py (service), warmup.py (router), main.py, models/__init__.py, migrations/038_warmup_settings.sql
- Anti-patterns: 0 blockers, 0 stubs
- Overall: PHASE GOAL ACHIEVED

---

*Verified: 2026-06-29T14:30:00Z*
*Verifier: Claude (gsd-verifier)*
