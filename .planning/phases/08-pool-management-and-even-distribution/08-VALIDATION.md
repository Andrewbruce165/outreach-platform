---
phase: 8
slug: pool-management-and-even-distribution
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-06-23
---

# Phase 8 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest + pytest-asyncio (repo) |
| **Config file** | `tests/conftest.py` (session DB setup + factories) + `docker-compose.test.yml` overlay |
| **Quick run command** | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_pool_endpoints.py tests/test_rebalance.py -x` |
| **Full suite command** | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest` |
| **Estimated runtime** | ~60–120 seconds (full suite) |

> **NEVER** run `docker compose run --rm api pytest` without the test overlay — DATABASE_URL would point at prod and conftest would DROP SCHEMA. Always use the overlay above.

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/test_pool_endpoints.py tests/test_rebalance.py -x` (via overlay)
- **After every plan wave:** Run full suite (includes existing `test_sender_lock.py`, `test_campaign_router.py`, `test_rotation_campaign.py` to catch contract regressions)
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** ~120 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 8-W0-01 | W0 | 0 | POOL-01..09 | — | N/A | fixture | `pytest tests/test_pool_endpoints.py --collect-only` | ❌ W0 | ⬜ pending |
| 8-att-01 | attach | — | POOL-01 | — | attach only to workspace-owned sender | integration | `pytest tests/test_pool_endpoints.py::test_attach_adds_sender -x` | ❌ W0 | ⬜ pending |
| 8-att-02 | attach | — | POOL-02 | — | sender already in other running campaign → 409 SENDER_LOCK_CONFLICT (`conflicts:[{sender_id,campaign_id,campaign_name}]`) | integration | `pytest tests/test_pool_endpoints.py::test_attach_locked_sender_409 -x` | ❌ W0 | ⬜ pending |
| 8-att-03 | attach | — | POOL-03 | — | foreign-workspace sender → 404, no leak | integration | `pytest tests/test_pool_endpoints.py::test_attach_foreign_sender_404 -x` | ❌ W0 | ⬜ pending |
| 8-det-01 | detach | — | POOL-04 | — | detach removes `campaign_senders` row | integration | `pytest tests/test_pool_endpoints.py::test_detach_removes_sender -x` | ❌ W0 | ⬜ pending |
| 8-det-02 | detach | — | POOL-05 | — | detach last sender of running → 409 MIN_POOL_GUARD | integration | `pytest tests/test_pool_endpoints.py::test_detach_last_running_409 -x` | ❌ W0 | ⬜ pending |
| 8-det-03 | detach | — | POOL-06 | — | detach with un-sent cold pending → 409 DETACH_BLOCKED_PENDING | integration | `pytest tests/test_pool_endpoints.py::test_detach_cold_pending_409 -x` | ❌ W0 | ⬜ pending |
| 8-det-04 | detach | — | POOL-06b | — | detach allowed when only engaged dialogs remain (D-05) | integration | `pytest tests/test_pool_endpoints.py::test_detach_engaged_only_ok -x` | ❌ W0 | ⬜ pending |
| 8-reb-01 | rebalance | — | POOL-07 | — | attach to running with skewed backlog moves cold-pending toward even split (±1 of total/P) | integration | `pytest tests/test_rebalance.py::test_rebalance_evens_cold_pending -x` | ❌ W0 | ⬜ pending |
| 8-reb-02 | rebalance | — | POOL-08 | — | rebalance idempotent (second call moves 0) | integration | `pytest tests/test_rebalance.py::test_rebalance_idempotent -x` | ❌ W0 | ⬜ pending |
| 8-reb-03 | rebalance | — | POOL-08b | — | rebalance never moves sent/processing/engaged rows | integration | `pytest tests/test_rebalance.py::test_rebalance_skips_non_cold -x` | ❌ W0 | ⬜ pending |
| 8-fe-01 | frontend | — | POOL-09 | — | panel attach/detach + human-readable 409 render | manual | UAT in `campaigns.$id` Senders panel | manual | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_pool_endpoints.py` — stubs for POOL-01..06b (attach/detach + guards). Reuse existing fixtures: `async_client`, `valid_supabase_jwt`, `test_running_campaign_factory`, `attach_sender_to_campaign`, `test_sender_factory`, `test_campaign_factory`.
- [ ] `tests/test_rebalance.py` — stubs for POOL-07/08/08b (even-split + idempotency + safety).
- [ ] `tests/conftest.py` — add `test_queue_item_factory(campaign_id, sender_id, recipient_phone, status='pending')` to seed `message_queue` rows + matching `campaign_contact_assignments` rows for a running campaign.
- [ ] Framework install: none — pytest infra already present (13+ campaign test files use it).

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Senders/Пул panel attach/detach UX | POOL-09 | Frontend lives in separate Lovable repo `aimly-tg-outreach`; visual + interaction only verifiable in-browser | Open a campaign page, add a 2nd sender via multiselect/chips, confirm it appears; try detaching last sender / a locked sender and confirm human-readable 409 (sender-locked / min-pool / detach-blocked) renders. |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references (two test files + `test_queue_item_factory`)
- [ ] No watch-mode flags
- [ ] Feedback latency < 120s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
