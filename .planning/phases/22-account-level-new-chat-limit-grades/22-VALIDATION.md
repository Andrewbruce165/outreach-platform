---
phase: 22
slug: account-level-new-chat-limit-grades
status: final
nyquist_compliant: true
wave_0_complete: true
created: 2026-07-08
---

# Phase 22 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest + pytest-asyncio (async fixtures), asyncpg |
| **Config file** | `tests/conftest.py` (session-scoped `_setup_database`, ephemeral `outreach_test` DB) |
| **Quick run command** | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_queue_new_dialog_limit.py tests/test_queue_even_pacing.py -x` |
| **Full suite command** | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest` |
| **Estimated runtime** | ~30-60 seconds (targeted subset) |

> **Baseline caution (project memory `project-test-baseline-red`):** the full suite is order-dependent and RED on clean main (~88 failed/115 errors) while the same files pass in isolation. Do NOT trust full-suite exit code as a phase gate — run the targeted subset below, and diff against a clean-tree run of the same subset.

---

## Sampling Rate

- **After every task commit:** Run the targeted file(s) for that task, `-x`.
- **After every plan wave:** Run all Phase-22-touched test files as a targeted set (NOT the full suite — baseline is RED).
- **Before `/gsd:verify-work`:** Targeted set green + clean-tree diff shows no regression in the touched files.
- **Max feedback latency:** 60 seconds.

---

## Per-Task Verification Map

| Task ID | Decision | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|----------|-------------|-----------|-------------------|-------------|--------|
| 22-01-01 | D-14/D-10 | sender grade columns (current_level/level_updated_at) + backfill to created_at | grep/migration | `grep -n "current_level" app/models/__init__.py && grep -c "IF NOT EXISTS" migrations/056_sender_grade_columns.sql` | ✅ create | ⬜ pending |
| 22-01-02 | D-16/D-08 | sender_first_contacts (idempotent backfill) + sender_grade_settings + grade_ladder defaults 5/30,9/30,13 | grep/migration | `grep -c "ON CONFLICT DO NOTHING" migrations/057_sender_first_contacts.sql && grep -n "sender_grade_settings" app/models/__init__.py` | ✅ create | ⬜ pending |
| 22-01-03 | D-14/D-16/D-08 | foundation: fresh test DB has grade columns/tables + conftest SQL-only blocks | integration | `pytest tests/test_grade_foundation.py -x` | ❌ W0 (new) | ⬜ pending |
| 22-02-01 | D-16 | ladder GET/PUT, code-defaults on absent row, green-corridor warnings, workspace-scoped upsert | integration | `grep -n "ON CONFLICT (workspace_id) DO UPDATE" app/routers/grade_settings.py && grep -n "grade_settings.router" app/main.py` | ✅ create | ⬜ pending |
| 22-02-02 | D-14/D-17 | auto-progression sweep advances level after step days; stops at 3 | integration | `grep -n "grade_progression_worker.start()" app/main.py && grep -n "current_level < 3" app/services/grade_progression.py` | ✅ create | ⬜ pending |
| 22-02-03 | D-16/D-14/D-17 | ladder GET/PUT defaults + cross-tenant isolation + progression/stop-at-3 tests | integration | `pytest tests/test_grade_settings.py tests/test_grade_progression.py -x` | ❌ W0 (new) | ⬜ pending |
| 22-03-01 | D-01/D-06/D-13/D-05 | sender-wide DISTINCT-phone cap across campaigns; account grade budget as cap RHS + pace numerator; campaign window preserved | integration | `pytest tests/test_queue_new_dialog_limit.py tests/test_queue_even_pacing.py -x` | ✅ extend | ⬜ pending |
| 22-03-02 | D-04 | rate_per_day gate removed from _check_rate_limits; min/hour/15-per-hour + interval floor intact | grep/integration | `grep -c "rate_per_day\|max_per_day" app/services/queue.py; pytest tests/test_send.py -k rate -x` | ✅ extend | ⬜ pending |
| 22-03-03 | D-01/D-05/D-13/D-04 | extend queue/pacing/rate tests for account-wide behavior | integration | `pytest tests/test_queue_new_dialog_limit.py tests/test_queue_even_pacing.py tests/test_send.py -k "rate or sender_wide or budget or pacing" -x` | ✅ extend | ⬜ pending |
| 22-04-01 | D-04 | sender API no longer exposes/validates/accepts rate_per_day (per_day) | grep | `grep -c "rate_per_day\|per_day" app/routers/senders.py; grep -n "per_day" app/schemas/__init__.py` | ✅ edit | ⬜ pending |
| 22-04-02 | D-12/D-15 | SenderResponse grade fields (current_level/level_updated_at/remaining budget) + PATCH /senders/{slug}/grade override resets timer | grep/integration | `grep -n "current_level" app/schemas/__init__.py && grep -n "senders/{slug}/grade" app/routers/senders.py` | ✅ edit | ⬜ pending |
| 22-04-03 | D-04/D-12/D-15 | test_senders.py: rate removal + grade fields + override + out-of-range reject | integration | `pytest tests/test_senders.py -k "rate or grade or override" -x` | ✅ extend | ⬜ pending |
| 22-05-01 | D-08 | new-pair charges initiator + registry insert; known pair free | integration | `grep -n "sender_first_contacts" app/services/warmup.py && pytest tests/test_warmup_worker.py -k pair -x` | ✅ extend | ⬜ pending |
| 22-05-02 | D-09/D-03 | outreach-priority reserve on shared trailing-24h budget; warmup gets remainder | integration | `grep -n "INTERVAL '24 hours'" app/services/warmup.py && pytest tests/test_warmup_worker.py -k reserve -x` | ❌ W0 (extend) | ⬜ pending |
| 22-05-03 | D-08/D-09 | warmup budget/reserve/idempotent-backfill tests | integration | `pytest tests/test_warmup_worker.py -k "pair or reserve" -x` | ✅ extend | ⬜ pending |
| 22-06-01 | D-04/D-07 | migration 059 drops campaigns.max_new_dialogs_per_day + senders.rate_per_day from DB + ORM | grep/migration | `grep -c "DROP COLUMN IF EXISTS" migrations/059_drop_dead_limit_columns.sql; grep -c "rate_per_day\|max_new_dialogs_per_day" app/models/__init__.py` | ✅ create | ⬜ pending |
| 22-06-02 | D-07 | campaign schema + router cleanup (no dialog-limit field/validation) | grep | `grep -rc "max_new_dialogs_per_day\|_validate_max_new_dialogs\|DIALOG_LIMIT" app/routers/campaigns.py app/schemas/__init__.py` | ✅ edit | ⬜ pending |
| 22-06-03 | D-07/D-04 | conftest note + campaign tests + repo-wide grep gate (no references remain) | integration | `test -z "$(grep -rn --include=*.py 'max_new_dialogs_per_day\|rate_per_day' app/)" && pytest tests/test_send_campaign.py -x` | ✅ extend | ⬜ pending |
| 22-07-01 | D-12 | openapi.json + types regenerated: grade endpoints/fields in, retired fields out | grep | `grep -c "sender-grade-settings" lovable-handoff/openapi.json; grep -c "max_new_dialogs_per_day" lovable-handoff/openapi.json` | ✅ regen | ⬜ pending |
| 22-07-02 | D-11 | frontend handoff note references grade endpoints + lists removals | file/grep | `test -f 22-FRONTEND-HANDOFF.md && grep -c "sender-grade-settings\|current_level\|/grade" 22-FRONTEND-HANDOFF.md` | ✅ create | ⬜ pending |
| 22-07-03 | D-11/D-15/D-16 | human-verify: ladder editor, grade display, override, field removals (UI in sibling repo) | manual (checkpoint) | manual-only — see Manual-Only Verifications | — | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*
*Task IDs are real `{phase}-{plan}-{task}` IDs pulled from the 7 executed PLAN.md files. Every code-producing task carries an `<automated>` verify; Wave-0 gaps (new test files) are created by the tdd-typed foundation/test tasks in their owning plans.*

---

## Wave 0 Requirements

- [ ] `tests/test_grade_settings.py` — ladder GET/PUT + code-defaults (mirror `tests/test_warmup_router.py`).
- [ ] Grade progression/override cases in `tests/test_senders.py` (D-14/D-15).
- [ ] Sender-wide dedup + account-budget cases extending `tests/test_queue_new_dialog_limit.py` (D-13/D-01/D-06).
- [ ] New-pair budget + reserve cases extending `tests/test_warmup_worker.py` (D-08/D-09).
- [ ] Idempotent-backfill assertion for `sender_first_contacts` (existing warmed pairs not counted as new).
- [ ] conftest exists-guarded blocks for any SQL-only migration (backfill/CHECK) the above tests depend on.
- [ ] Helpers for pacing/dialog tests are copied verbatim between `test_queue_new_dialog_limit.py` and `test_queue_even_pacing.py` — extend both consistently.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|--------------------|
| Grade + budget remainder display on sender card | D-11 (UI) | Frontend lives in separate Lovable repo, no automated cross-repo UI test harness | Open sender card in UI, confirm current grade/level, progress to next level, and remaining daily budget render correctly |
| Manual grade override control in UI | D-15 (UI) | Same as above — frontend-only interaction | Trigger override from sender card, confirm grade changes and timer resets, confirm subsequent auto-progression uses new baseline |
| Ladder settings editor (3 rows: limit + step days) with green-corridor warnings | D-16 (UI) | Same as above | Edit ladder values in/out of recommended range, confirm warning UI matches existing rate-limit warning patterns |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies (22-07-03 is a human-verify checkpoint, manual-only by design)
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references (new test files 22-01-03/22-02-03 + reserve cases 22-05-02)
- [x] No watch-mode flags
- [x] Feedback latency < 60s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** finalized 2026-07-08 against the 7 executed PLAN.md files (Dimension 8 passes)
