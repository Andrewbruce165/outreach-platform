---
phase: 19
slug: no-reply-follow-up-and-auto-finish
status: planned
nyquist_compliant: true
wave_0_complete: false
created: 2026-07-02
---

# Phase 19 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (async, via docker test-overlay) |
| **Config file** | pyproject.toml + tests/conftest.py |
| **Quick run command** | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/<target> -q` |
| **Full suite command** | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest -q` |
| **Estimated runtime** | ~300 seconds (full suite) |

---

## Sampling Rate

- **After every task commit:** Run the quick command scoped to the touched test files
- **After every plan wave:** Run the full suite command
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 300 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 19-01-T1 | 01 | 1 | NORP-01 | mig/unit | `pytest tests/test_follow_up.py::test_no_reply_status_allowed -q` | ❌→Wave 0 | ⬜ pending |
| 19-01-T2 | 01 | 1 | NORP-02,03 | schema | `grep server_default app/models/__init__.py` (ORM mirror) | n/a | ⬜ pending |
| 19-01-T3 | 01 | 1 | all | scaffold | `pytest tests/test_follow_up.py --collect-only -q` | ❌→Wave 0 | ⬜ pending |
| 19-02-T1 | 02 | 2 | NORP-02 | api | `pytest tests/test_follow_up.py::test_campaign_follow_up_fields -q` | ✅ (after 01) | ⬜ pending |
| 19-02-T2 | 02 | 2 | NORP-02 | api | `pytest tests/test_follow_up.py::test_campaign_follow_up_fields -q` | ✅ | ⬜ pending |
| 19-02-T3 | 02 | 2 | NORP-05 | unit | `pytest tests/test_follow_up.py -k ping -q` | ✅ | ⬜ pending |
| 19-03-T1 | 03 | 2 | NORP-07 | integration | `pytest tests/test_follow_up.py::test_reply_cancels_pings -q` | ✅ | ⬜ pending |
| 19-03-T2 | 03 | 2 | NORP-08 | integration | `pytest tests/test_queue_even_pacing.py -k ping -q` | ✅ | ⬜ pending |
| 19-04-T1 | 04 | 3 | NORP-06 | config | `grep FOLLOW_UP_TICK_SECONDS app/config.py` | n/a | ⬜ pending |
| 19-04-T2 | 04 | 3 | NORP-04,06,09,10,11,12 | integration | `pytest tests/test_follow_up.py -k "ping_on_interval or auto_finish or finish_reason or paused" -q` | ✅ | ⬜ pending |
| 19-04-T3 | 04 | 3 | NORP-04 | lifespan | `pytest -q` (full suite; worker starts) | ✅ | ⬜ pending |
| 19-05-T1 | 05 | 4 | NORP-13 | contract | `grep follow_up_interval_hours lovable-handoff/openapi.json` | n/a | ⬜ pending |
| 19-05-T2 | 05 | 4 | NORP-13 | frontend | `cd /root/apps/aimly/aimly-tg-outreach && tsc` clean | n/a | ⬜ pending |
| 19-05-T3 | 05 | 4 | NORP-13 | human-verify | manual (campaign form + live no_reply flow) | n/a | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

Wave 0 lands in Plan 19-01, Task 3 (RED scaffold + migration registration):
- [ ] `tests/test_follow_up.py` — covers NORP-01,02,04,06,07,12 (new file; RED scaffold with deferred in-body imports per Phase 13/17 precedent)
- [ ] Queue guard test for NORP-08 in `tests/test_queue_even_pacing.py` or `tests/test_follow_up.py`
- [ ] Migration 045 registered in `tests/conftest.py` so the ephemeral test DB has the no_reply CHECK + new columns
- [ ] Reuse existing `test_running_campaign_factory` / `test_conversation_factory` / `test_queue_item_factory`
- [ ] No framework install needed — pytest + overlay already present

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Campaign form Follow Up block renders + persists; bounds enforced in UI | NORP-13 | Visual UI + Lovable frontend behavior | Plan 19-05 Task 3 checkpoint steps 1-3 |
| Live no_reply → ping (same sender) → reply reverts → auto-finish closes | NORP-04/07/09/10 | Requires real Telegram accounts + wall-clock timer | Plan 19-05 Task 3 checkpoint steps 4-5 (optional live) |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references (test_follow_up.py + mig 045 in conftest)
- [x] No watch-mode flags
- [x] Feedback latency < 300s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** planner-signed 2026-07-02
