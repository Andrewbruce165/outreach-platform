---
phase: 9
slug: cold-contact-failover
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-06-24
---

# Phase 9 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest + pytest-asyncio (`pytestmark = pytest.mark.asyncio`) |
| **Config file** | `tests/conftest.py` (ephemeral postgres via `docker-compose.test.yml`) |
| **Quick run command** | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_failover.py -x` |
| **Full suite command** | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest` |
| **Estimated runtime** | quick ~15s · full ~3-4 min (~683 collected) |

> **CLAUDE.md hard rule:** NEVER `docker compose run --rm api pytest` without the test-overlay — the conftest guard (tests/conftest.py:46-77) blocks it, but the correct path is always the overlay (DATABASE_URL → ephemeral `outreach_test`, tmpfs, auto-removed). A 2026-05-26 incident wiped prod via this exact mistake.

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/test_failover.py -x` (overlay)
- **After every plan wave:** Run full suite (overlay) — must stay green
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** ~15 seconds (quick), ~4 min (full)

---

## Per-Task Verification Map

| Requirement | Behavior | Test Type | Automated Command | File Exists | Status |
|-------------|----------|-----------|-------------------|-------------|--------|
| FAIL-01 | frozen backlog spreads to healthy pool | unit | `pytest tests/test_failover.py::test_failover_spreads_to_healthy_pool -x` | ❌ W0 | ⬜ pending |
| FAIL-01/D-09 | frozen sender excluded as receiver (Pitfall 1) | unit | `pytest tests/test_failover.py::test_failover_excludes_frozen_as_receiver -x` | ❌ W0 | ⬜ pending |
| FAIL-02 | each of 3 call sites invokes failover | integration | `pytest tests/test_failover.py::test_peer_flood_triggers_failover -x` (+ frozen + antispam) | ❌ W0 | ⬜ pending |
| FAIL-03 | predicate moves only cold-pending | unit | `pytest tests/test_failover.py::test_failover_skips_engaged -x` | ❌ W0 | ⬜ pending |
| FAIL-03/D-05 | empty conversation IS movable | unit | `pytest tests/test_failover.py::test_failover_moves_empty_conversation -x` | ❌ W0 | ⬜ pending |
| FAIL-04 | queue + CCA in sync after move | unit | `pytest tests/test_failover.py::test_failover_cca_in_sync -x` | ❌ W0 | ⬜ pending |
| FAIL-05 | engaged dialog stays on frozen sender | unit | `pytest tests/test_failover.py::test_failover_leaves_engaged -x` | ❌ W0 | ⬜ pending |
| FAIL-06 | idempotent (2nd call moves 0) | unit | `pytest tests/test_failover.py::test_failover_idempotent -x` | ❌ W0 | ⬜ pending |
| FAIL-07 | no healthy receiver → rows stay paused | unit | `pytest tests/test_failover.py::test_failover_no_receiver_keeps_paused -x` | ❌ W0 | ⬜ pending |
| FAIL-08 | logs COUNT + sender UUIDs only (no PII) | unit | `pytest tests/test_failover.py::test_failover_logs_count_no_pii -x` | ❌ W0 | ⬜ pending |
| FAIL-09 | no migration — operates on existing columns | manual | n/a — verified by absence of new `migrations/*.sql` | n/a | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_failover.py` — RED stubs covering FAIL-01..FAIL-09 (import-inside-body pattern from `tests/test_rebalance.py:51` so `--collect-only` stays clean)
- [ ] Fixture extension: `test_queue_item_factory` (conftest.py:600) supports `with_conversation` but inserts NO `messages` row. To test D-05 (empty conversation movable) vs engaged (has message), add an optional `with_message=True` flag (or use `test_conversation_factory` at conftest.py:696 + a `messages` insert). Empty-conversation case is already producible (`with_conversation=True, with_message=False`); only the has-message case needs new fixture support.
- [ ] Reuse `test_running_campaign_factory(sender_count=N)` (conftest.py:680) and `_pending_counts`/`_cca_sender_for` helpers (test_rebalance.py:26-41) — copy them.
- Framework install: none (pytest-asyncio + overlay already in place).

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| No new migration introduced | FAIL-09 | Absence-of-artifact check | Confirm `git status migrations/` shows no new `*.sql` for this phase; failover uses only pre-existing columns (`sender_id`, `restriction_status`, `scheduled_at`, CCA). |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s (quick)
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
