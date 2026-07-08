---
phase: 22
slug: account-level-new-chat-limit-grades
status: draft
nyquist_compliant: false
wave_0_complete: false
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
| 22-xx | D-13 | sender-wide dedup: phone contacted in campaign A blocks re-open in campaign B | integration | `pytest tests/test_queue_new_dialog_limit.py -k sender_wide` | ❌ W0 (extend existing) | ⬜ pending |
| 22-xx | D-01/D-06 | account budget cap counts across campaigns, not per-campaign | integration | `pytest tests/test_queue_new_dialog_limit.py` | ✅ extend | ⬜ pending |
| 22-xx | D-05 | pacing numerator = account budget, campaign window preserved | integration | `pytest tests/test_queue_even_pacing.py` | ✅ extend | ⬜ pending |
| 22-xx | D-04 | rate_per_day gate removed; min/hour/15-per-hour intact | integration | `pytest tests/test_senders.py tests/test_send.py -k rate` | ✅ extend | ⬜ pending |
| 22-xx | D-14 | auto-progression advances level after step days; stops at 3 | unit/integration | `pytest tests/test_senders.py -k grade` | ❌ W0 | ⬜ pending |
| 22-xx | D-15 | manual override sets level + resets timer | integration | `pytest tests/test_senders.py -k override` | ❌ W0 | ⬜ pending |
| 22-xx | D-16 | ladder GET/PUT, code-defaults on absent row | integration | `pytest tests/test_grade_settings.py` (new, mirror `test_warmup_router.py`) | ❌ W0 | ⬜ pending |
| 22-xx | D-08 | new warmup pair charges initiator; known pair free; backfill idempotent | integration | `pytest tests/test_warmup_worker.py -k pair` | ✅ extend | ⬜ pending |
| 22-xx | D-09 | outreach reserve leaves warmup only the remainder | integration | `pytest tests/test_warmup_worker.py -k reserve` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*
*Task IDs are placeholders — gsd-planner fills in real `{N}-{plan}-{task}` IDs against this decision map.*

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

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 60s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
