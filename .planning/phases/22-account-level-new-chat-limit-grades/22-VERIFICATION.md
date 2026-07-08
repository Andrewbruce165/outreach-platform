---
phase: 22-account-level-new-chat-limit-grades
verified: 2026-07-08T23:14:18Z
status: passed
score: 17/17 must-haves verified
behavior_unverified: 0
overrides_applied: 0
---

# Phase 22: Account-level new-chat limit grades Verification Report

**Phase Goal:** Лимит «новых чатов в сутки» переезжает с кампании (`campaigns.max_new_dialogs_per_day`) на аккаунт (sender) — единый глобальный счётчик, общий для рассылки и warmup-паринга. Грейд растёт автоматически по возрасту аккаунта шагом 30 дней: 0–30д → 5/день, 30–60д → 9/день, 60д+ → 13/день. Отдельно убирается лимит «исходящих сообщений в сутки» (`senders.rate_per_day`/150) из backend и UI целиком.

**Verified:** 2026-07-08T23:14:18Z
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths (aggregated must_haves.truths from all 7 plan frontmatters)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | `senders.current_level`/`level_updated_at` exist, backfilled to `created_at` (D-14/D-10) | ✓ VERIFIED | Migration 056 applied in prod (`schema_migrations` row `applied_at=2026-07-08 18:33:29`); `\d senders` on live prod DB shows both columns NOT NULL with CHECK `current_level BETWEEN 1 AND 3`; ORM `app/models/__init__.py:130-132` carries `server_default` |
| 2 | `sender_first_contacts` exists, idempotently backfilled from prior warmup pairs (D-08) | ✓ VERIFIED | Migration 057 applied in prod; table exists with canonical `(sender_a_id,sender_b_id)` PK; backfill INSERTs from `warmup_sessions`+`warmup_messages` with `ON CONFLICT DO NOTHING`, `LEAST/GREATEST` canonicalization |
| 3 | `sender_grade_settings` exists, per-workspace 3-level ladder, code-defaults 5/30,9/30,13 (D-16) | ✓ VERIFIED | Migration 058 applied in prod; `\d sender_grade_settings` confirms columns+defaults; `app/services/grade_ladder.py::LADDER_DEFAULTS = [(5,30),(9,30),(13,None)]`, `resolve_ladder(None)` returns defaults |
| 4 | GET/PUT `/sender-grade-settings` workspace-scoped, resolves defaults, green-corridor warnings (D-16) | ✓ VERIFIED | `app/routers/grade_settings.py` — `ctx.workspace_id` scoping, `ON CONFLICT (workspace_id) DO UPDATE`, `_validate_ladder` WarningItem list; live prod `/openapi.json` exposes `/api/v1/sender-grade-settings`; human live-tested PUT round-trip against live DB (22-07-SUMMARY) |
| 5 | Background sweep advances `current_level`/`level_updated_at` at step_days elapsed; level 3 never advances (D-14/D-17) | ✓ VERIFIED | `app/services/grade_progression.py::_SWEEP_SQL` — set-based UPDATE keyed on `s.current_level < 3` and `NOW() - level_updated_at >= make_interval(...)` reading per-workspace ladder; wired into `app/main.py` lifespan (`grade_progression_worker.start()/stop()`); `tests/test_grade_progression.py` 5/5 green |
| 6 | New-dialog cap counts DISTINCT `recipient_phone` sender-wide, not per-campaign (D-01/D-06/D-13) | ✓ VERIFIED | `app/services/queue.py` cap COUNT keyed on `opened.sender_id = mq.sender_id` only (no `campaign_id` filter); `grep -c "opened.campaign_id = mq.campaign_id"` = 0 |
| 7 | A phone already `sent` to in ANY campaign is a known peer sender-wide, spends no budget elsewhere (D-13) | ✓ VERIFIED | EXISTS subquery keyed on `prior.sender_id = mq.sender_id AND prior.recipient_phone = mq.recipient_phone` — no `campaign_id` predicate; `tests/test_queue_new_dialog_limit.py` 7/7 green |
| 8 | Cap RHS + pace numerator use the account grade budget, not `campaigns.max_new_dialogs_per_day` (D-01/D-05) | ✓ VERIFIED | `account_budget = grade_ladder.budget_for_level(ladder, camp_row.s_level)` resolved pre-tick and bound as `:account_budget`/used in `expected_now`; `c.max_new_dialogs_per_day` no longer selected |
| 9 | Campaign working window (tz/hours) preserved for pacing (D-05) | ✓ VERIFIED | `_window_elapsed_fraction(campaign_tz=camp_row.c_tz, work_hour_start=camp_row.c_whs, work_hour_end=camp_row.c_whe, ...)` unchanged call shape; `_campaign_in_working_window` still gates final pick |
| 10 | `_check_rate_limits` no longer reads/gates `rate_per_day`; min/hour/MAX_NEW_CONTACTS_PER_HOUR/interval floor untouched (D-04) | ✓ VERIFIED | `SELECT rate_per_min, rate_per_hour, ...` (no `rate_per_day`); `MAX_NEW_CONTACTS_PER_HOUR = 15` intact; docstring cites Phase 22 D-04 removal |
| 11 | Sender API no longer exposes/accepts `rate_per_day` (D-04) | ✓ VERIFIED | `grep -c "rate_per_day" app/routers/senders.py` = 0; `app/schemas/__init__.py` has no `per_day` in `RateLimits`/`SenderCreate`/`SenderUpdate`; live prod `/openapi.json` confirms `rate_per_day` absent |
| 12 | `SenderResponse` carries `current_level`/`level_updated_at`/remaining daily budget (D-12) | ✓ VERIFIED | `app/schemas/__init__.py:159-164`; `_sender_to_response`/`_remaining_budget` in `app/routers/senders.py`; live `/openapi.json` shows `current_level`(5 hits)/`remaining_daily_budget`(1)/`level_updated_at`(3) |
| 13 | PATCH `/senders/{slug}/grade` sets `current_level`+resets `level_updated_at=NOW()`, workspace-scoped, rejects out-of-range (D-15) | ✓ VERIFIED | `app/routers/senders.py:734-769` — `_load_sender_by_slug` (workspace-scoped 404), single UPDATE writing both fields; `GradeOverrideRequest.current_level: int = Field(..., ge=1, le=3)` |
| 14 | New warmup pair charges initiator's budget + records pair; known pair spends nothing (D-08) | ✓ VERIFIED | `app/services/warmup.py:_create_new_sessions` — `known_pairs` set from `sender_first_contacts`, `is_new_pair` branch charges + `INSERT ... LEAST/GREATEST ... ON CONFLICT DO NOTHING`; `tests/test_warmup_worker.py -k pair` green |
| 15 | Warmup only opens new pair with remaining budget after outreach reserve; reserve = trailing-24h shared window (D-09/D-03) | ✓ VERIFIED | `_remaining_new_chat_budget` — `account_budget - spent_24h - pending`, `spent` counted via `finished_at >= NOW() - INTERVAL '24 hours'` matching the queue cap window exactly |
| 16 | `campaigns.max_new_dialogs_per_day` and `senders.rate_per_day` dropped from DB/ORM/schemas/router (D-07/D-04) | ✓ VERIFIED | Migration 059 applied in prod (`schema_migrations` row); live prod `\d campaigns`/`\d senders` show columns absent; `grep -rn "rate_per_day\|max_new_dialogs_per_day" app/` = 0 hits repo-wide |
| 17 | Frontend (sibling repo) delivers ladder editor, per-card grade+override, removed rate/cap fields (D-11); openapi.json regenerated (D-12) | ✓ VERIFIED | `lovable-handoff/openapi.json` — 1 hit `sender-grade-settings`, 0 hits `max_new_dialogs_per_day`/`rate_per_day`; sibling repo `aimly-tg-outreach` commit `60f3d3b` (pushed, `HEAD==origin/main`) adds Settings→Grade Ladder tab, sender-card grade/override, removes campaign cap field + sender rate display; human live-verified PUT round-trip against prod DB |

**Score:** 17/17 truths verified (0 present-but-behavior-unverified)

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `migrations/056_sender_grade_columns.sql` | grade columns + backfill | ✓ VERIFIED | Idempotent, applied in prod |
| `migrations/057_sender_first_contacts.sql` | pair registry + backfill | ✓ VERIFIED | Idempotent, applied in prod |
| `migrations/058_sender_grade_settings.sql` | workspace ladder table | ✓ VERIFIED | Idempotent, applied in prod |
| `migrations/059_drop_dead_limit_columns.sql` | drop both dead columns | ✓ VERIFIED | Idempotent, applied in prod |
| `app/services/grade_ladder.py` | shared ladder resolver | ✓ VERIFIED | `LADDER_DEFAULTS`, `resolve_ladder`, `budget_for_level`, `step_days_for_level`, `load_ladder` — imported by queue.py/warmup.py/senders.py/grade_settings.py/grade_progression.py |
| `app/services/grade_progression.py` | auto-progression sweep worker | ✓ VERIFIED | Set-based hourly sweep, wired into `app/main.py` lifespan |
| `app/routers/grade_settings.py` | GET/PUT ladder API | ✓ VERIFIED | Registered in `app/main.py`; live on prod `/openapi.json` |
| `app/services/queue.py` | account-budget + sender-wide rewrite | ✓ VERIFIED | All three subqueries (EXISTS/cap/pace) sender-wide; `rate_per_day` gate removed |
| `app/services/warmup.py` | new-pair budget + outreach reserve | ✓ VERIFIED | `_pick_initiator`, `_remaining_new_chat_budget`, registry read/write in `_create_new_sessions` |
| `app/routers/senders.py` + `app/schemas/__init__.py` | grade surface + rate_per_day removal | ✓ VERIFIED | PATCH `/grade`, `SenderResponse` grade fields, `rate_per_day` gone |
| `lovable-handoff/openapi.json` / `types/api.ts` | regenerated contract | ✓ VERIFIED | Grade endpoints present, dead fields absent, matches live prod api |
| `22-FRONTEND-HANDOFF.md` | frontend handoff note | ✓ VERIFIED | Present, documents 3 UI deliverables + removals |
| sibling repo UI (`aimly-tg-outreach`) | Grade Ladder tab, card grade+override, removed fields | ✓ VERIFIED | Commit `60f3d3b`, pushed to `origin/main`, diff-reviewed directly |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| `app/services/queue.py` pre-query | `app/services/grade_ladder.py::load_ladder` | Python call resolving `account_budget` per tick | ✓ WIRED | `ladder = await grade_ladder.load_ladder(db, camp_row.s_wid)` then `budget_for_level` |
| `app/services/queue.py` main SELECT | `:account_budget`/`:expected_now` binds | `CAST(... AS INTEGER/DOUBLE PRECISION)` | ✓ WIRED | Both casts present, explanatory comment preserved (untyped-bind truncation pitfall) |
| `app/services/warmup.py::_create_new_sessions` | `sender_first_contacts` | known/new pair classification + insert | ✓ WIRED | Read once per tick into `set[frozenset]`; insert on new-pair creation with `ON CONFLICT DO NOTHING` |
| `app/services/warmup.py::_remaining_new_chat_budget` | trailing-24h window | shared with queue cap | ✓ WIRED | Identical `finished_at >= NOW() - INTERVAL '24 hours'` predicate in both files |
| `app/routers/grade_settings.py` | `sender_grade_settings` table | `ON CONFLICT (workspace_id) DO UPDATE` | ✓ WIRED | Verified via `grep` + live prod GET/PUT round-trip (human-tested, DB-confirmed) |
| `app/routers/senders.py::override_sender_grade` | `_load_sender_by_slug` | workspace-scoped 404 guard | ✓ WIRED | Cross-tenant re-grade prevented |
| `app/main.py` | `grade_settings.router` / `grade_progression_worker` | `include_router` + lifespan start/stop | ✓ WIRED | Confirmed by grep + live prod `/openapi.json` exposing the routes |
| sibling `accounts.tsx` | `PATCH /api/v1/senders/{slug}/grade` | `gradeMut` mutation | ✓ WIRED | `applyFreshSender(fresh)` on success, matches backend contract |
| sibling `settings.tsx` (Grade Ladder tab) | `GET/PUT /api/v1/sender-grade-settings` | `useQuery`/`useMutation` | ✓ WIRED | Human live-verified round-trip via direct DB query after two saves |

### Behavioral Spot-Checks / Test Execution

Ran the phase's own test files through the mandatory test-overlay (never the bare `docker compose run`):

```
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest \
  tests/test_grade_foundation.py tests/test_grade_settings.py tests/test_grade_progression.py \
  tests/test_queue_new_dialog_limit.py tests/test_queue_even_pacing.py tests/test_warmup_worker.py \
  tests/test_senders.py tests/test_send_campaign.py -q
```

| Suite | Result |
|-------|--------|
| test_grade_foundation.py | 3/3 PASS |
| test_grade_settings.py | 5/5 PASS |
| test_grade_progression.py | 5/5 PASS |
| test_queue_new_dialog_limit.py | 7/7 PASS |
| test_queue_even_pacing.py | 9/9 PASS (see note below — 1 transient time-of-day failure investigated and cleared) |
| test_warmup_worker.py | 9/10 PASS (1 pre-existing unrelated failure — see note below) |
| test_senders.py | 27/27 PASS |
| test_send_campaign.py | 5/5 PASS |

**Investigated anomaly 1 — `test_queue_even_pacing.py::test_pace_counter_window_start`:** failed on first run (23:11 UTC) because the test's own workaround (`if cur_hour == 23: cur_hour = 22`, meant to dodge the day-wrap edge) puts the simulated campaign window entirely in the past when the wall clock is actually inside hour 23 — a pre-existing, wall-clock-hour-23 flaky test, **not** a Phase 22 regression. Confirmed by diffing this test's body against the pre-Phase-22 commit (`39b0add^`): byte-identical logic already had this bug before Phase 22 touched anything (Phase 22 only changed the unrelated `_set_cap` helper to target `sender_grade_settings` instead of the dropped campaign column). Re-ran a few minutes later (past :11) — PASSED. Debug instrumentation used to diagnose was fully reverted (`git diff` on the file is clean).

**Investigated anomaly 2 — `test_warmup_worker.py::test_restricted_sender_excluded` (WARM-14):** fails on every run — asserts `spam_limited` senders are excluded from the warmup pool, but production code (commit `6c67c962`, dated 2026-06-30, **before** Phase 22 started) intentionally *includes* `spam_limited` accounts in warmup (trust-recovery model, Phase 15 D-14). Confirmed via `git diff 39b0add^ HEAD -- app/services/warmup.py` that no Phase 22 commit touched the `restriction_status` WHERE clause. This is a stale pre-existing RED test contradicting already-deployed behavior, explicitly logged as an out-of-scope pre-existing failure in `.planning/phases/22-account-level-new-chat-limit-grades/deferred-items.md` (written during 22-05 execution) — not a Phase-22-introduced gap.

Neither anomaly is a must-have failure for this phase; both are pre-existing conditions independently confirmed and explicitly out of scope.

### Live Production Verification

- All 4 migrations (056–059) confirmed applied in `schema_migrations` on the live prod DB (`applied_at 2026-07-08 18:33:29`).
- `\d senders` / `\d campaigns` / `\d sender_first_contacts` / `\d sender_grade_settings` on live prod DB confirm the exact final schema (grade columns present, dead columns absent).
- Live prod api `/openapi.json` (`curl http://127.0.0.1:8005/openapi.json`) confirms `/api/v1/sender-grade-settings` and `/api/v1/senders/{slug}/grade` are live, and `max_new_dialogs_per_day`/`rate_per_day` are completely absent from the served contract.
- Human live-tested the Grade Ladder editor end-to-end against production (documented in 22-07-SUMMARY.md): two PUT saves, each independently confirmed via direct DB query to match exactly what the UI sent — GET/edit/PUT round-trip is proven live-effective, not just code-reviewed.

### Requirements Coverage (D-01..D-17)

| Decision | Description | Delivered by | Status |
|----------|-------------|---------------|--------|
| D-01 | Limit moves to account level | 22-03 | ✓ SATISFIED |
| D-02 | Superseded by D-14/D-15 (no separate deliverable) | — | N/A (explicitly superseded in CONTEXT.md) |
| D-03 | Global/shared trailing-24h budget (queue+warmup) | 22-03, 22-05 | ✓ SATISFIED |
| D-04 | Remove `rate_per_day` (backend+API+DB) | 22-03, 22-04, 22-06 | ✓ SATISFIED |
| D-05 | Preserve campaign working-window for pacing | 22-03 | ✓ SATISFIED |
| D-06 | FIFO distribution across campaigns (no new priority logic) | 22-03 | ✓ SATISFIED |
| D-07 | Drop `campaigns.max_new_dialogs_per_day` | 22-06 | ✓ SATISFIED |
| D-08 | `sender_first_contacts` new-pair registry + initiator charge | 22-01, 22-05 | ✓ SATISFIED |
| D-09 | Outreach priority reserve for warmup | 22-05 | ✓ SATISFIED |
| D-10 | Bulk-imported accounts start at grade 1 | 22-01 | ✓ SATISFIED |
| D-11 | Frontend UI (ladder editor, card grade+override) | 22-07 + sibling repo `60f3d3b` | ✓ SATISFIED |
| D-12 | Regenerate openapi.json + types | 22-07 | ✓ SATISFIED |
| D-13 | Sender-wide dedup for "known peer" | 22-03 | ✓ SATISFIED |
| D-14 | `current_level`/`level_updated_at` storage + auto-progression | 22-01, 22-02 | ✓ SATISFIED |
| D-15 | Manual grade override resets timer | 22-04 | ✓ SATISFIED |
| D-16 | Configurable per-workspace 3-level ladder | 22-01, 22-02 | ✓ SATISFIED |
| D-17 | Level 3 permanent (no further auto-progression) | 22-02 | ✓ SATISFIED |

No orphaned requirements — every D-ID cited in CONTEXT.md is claimed by at least one plan's `requirements` frontmatter and independently confirmed in code.

### Anti-Patterns Found

None. No `TBD`/`FIXME`/`XXX`/`TODO`/`HACK`/`PLACEHOLDER` markers in any file touched by this phase's 7 plans (migrations, `grade_ladder.py`, `grade_progression.py`, `grade_settings.py`, `queue.py`, `warmup.py`, `senders.py`, `schemas/__init__.py`). No stub returns, no hardcoded-empty response bodies gating real functionality.

### Human Verification Required

None outstanding. The one cross-repo UI checkpoint (Task 3 of 22-07) was already conversationally approved by the human on 2026-07-08 with a live end-to-end DB-confirmed round-trip (see 22-07-SUMMARY.md "Task 3 — Human Verify (APPROVED 2026-07-08)"), which this verification independently corroborated by re-diffing the sibling repo's shipped commit and re-querying the live prod API/DB directly.

### Gaps Summary

No gaps. All 17 aggregated must-have truths across the 7 plans are verified against live code, live production database schema, live production API contract, and the pushed sibling frontend commit — not just SUMMARY.md narrative. Two unrelated pre-existing test-suite anomalies were investigated to ground truth and confirmed to predate this phase (one time-of-day flaky pacing test, one stale WARM-14 guard already logged in this phase's own `deferred-items.md`); neither maps to any must-have of this phase and neither was introduced or worsened by it.

---

*Verified: 2026-07-08T23:14:18Z*
*Verifier: Claude (gsd-verifier)*
