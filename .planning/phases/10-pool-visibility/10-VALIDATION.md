---
phase: 10
slug: pool-visibility
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-06-24
---

# Phase 10 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest + pytest-asyncio (`asyncio_mode=auto`, session-scoped loop) |
| **Config file** | `pyproject.toml` `[tool.pytest.ini_options]` |
| **Quick run command** | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_restriction_audit.py tests/test_pool_health.py -x` |
| **Full suite command** | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest` |
| **Estimated runtime** | ~60–90 seconds (full suite) |

> NEVER `docker compose run --rm api pytest` without the test-overlay (prod DROP SCHEMA via conftest guard). NEVER `down -v` on this stack (wipes prod `postgres_data`). See CLAUDE.md → Тесты.

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/test_restriction_audit.py tests/test_pool_health.py -x` (test-overlay)
- **After every plan wave:** Run full suite (test-overlay)
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** ~90 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 10-W0 | scaffold | 1 | HLTH-01/02, D-03, POOLV-01/02 | — | N/A | unit | `pytest tests/test_restriction_audit.py tests/test_pool_health.py --collect-only` | ❌ W0 | ⬜ pending |
| HLTH-01a | audit | 2 | HLTH-01 | — | PEER_FLOOD write-point inserts `spam_limited`/`queue_error` event in same TX | unit | `pytest tests/test_restriction_audit.py::test_peer_flood_writes_event -x` | ❌ W0 | ⬜ pending |
| HLTH-01b | audit | 2 | HLTH-01 | — | reconcile `free` writes a `cleared` event | unit | `pytest tests/test_restriction_audit.py::test_reconcile_cleared_writes_event -x` | ❌ W0 | ⬜ pending |
| HLTH-01c | audit | 2 | HLTH-01 | — | reconcile still-limited WITHOUT date shift writes NO event (D-01) | unit | `pytest tests/test_restriction_audit.py::test_reconcile_no_shift_no_event -x` | ❌ W0 | ⬜ pending |
| HLTH-01d | audit | 2 | HLTH-01 | — | reconcile with forward date shift writes ONE `extension` event (D-01) | unit | `pytest tests/test_restriction_audit.py::test_reconcile_shift_writes_extension -x` | ❌ W0 | ⬜ pending |
| HLTH-01e | audit | 2 | HLTH-01 | — | event is append-only / survives subsequent state change | unit | `pytest tests/test_restriction_audit.py::test_events_append_only -x` | ❌ W0 | ⬜ pending |
| HLTH-02a | audit | 2 | HLTH-02 | — | event row carries `activity_slice` (sends_1h/24h, unique contacts, rate) computed at write time | unit | `pytest tests/test_restriction_audit.py::test_event_carries_activity_slice -x` | ❌ W0 | ⬜ pending |
| HLTH-02b | audit | 2 | HLTH-02 | — | `proxy` snapshot stored from `senders.proxy` | unit | `pytest tests/test_restriction_audit.py::test_event_carries_proxy_snapshot -x` | ❌ W0 | ⬜ pending |
| HLTH-02c | audit | 2 | HLTH-02 | — | slice counts only `message_type='sent'`, windowed correctly | unit | `pytest tests/test_restriction_audit.py::test_slice_windows_sent_only -x` | ❌ W0 | ⬜ pending |
| D-03 | audit | 2 | HLTH-01 | — | recipient-privacy logged `category='recipient_privacy'`, never flips `restriction_status`, filterable out | unit | `pytest tests/test_restriction_audit.py::test_recipient_privacy_separate_category -x` | ❌ W0 | ⬜ pending |
| HLTH-03 | endpoint | 3 | HLTH-03 | — | `GET /senders/{slug}/restriction-events` returns workspace-scoped history newest-first | integration | `pytest tests/test_restriction_audit.py::test_history_endpoint -x` | ❌ W0 | ⬜ pending |
| POOLV-01 | response | 3 | POOLV-01 | — | `pool_health` aggregate correct for all-active / partial / all-paused | integration | `pytest tests/test_pool_health.py::test_pool_health_states -x` | ❌ W0 | ⬜ pending |
| POOLV-02 | response | 3 | POOLV-02 | — | `attached_senders[]` carry `restriction_status`/`restricted_until` | integration | `pytest tests/test_pool_health.py::test_attached_senders_enriched -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky. Task IDs are indicative; planner assigns final plan/wave numbering.*

---

## Wave 0 Requirements

- [ ] `tests/test_restriction_audit.py` — RED stubs for HLTH-01 (event writes at all branches, append-only, no-shift suppression), HLTH-02 (slice + proxy snapshot), D-03 (category separation), HLTH-03 (history endpoint). Use import-inside-body pattern (per `tests/test_failover.py:1`, `tests/test_rebalance.py:51`).
- [ ] `tests/test_pool_health.py` — RED stubs for POOLV-01 (`pool_health` 3-state arithmetic) + POOLV-02 (per-sender enrichment in `CampaignResponse`).
- [ ] Reuse the queue-item factory from `tests/test_pool_endpoints.py` / `test_rebalance.py` (message_queue + sticky CCA + optional conversation); seed `messages_log` rows for slice assertions.
- [ ] No framework install needed — pytest infra exists.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| 3-state pool badge (green/yellow/red) renders on campaign page | POOLV-03 | Frontend in sibling repo `aimly-tg-outreach`, no backend pytest (matches Phase 08-04 cross-repo UAT) | Attach ≥2 senders to a campaign, force one into `spam_limited` (restricted_until future) → campaign page shows yellow "K из N на паузе до T"; clear it → green; pause all → red |
| Account-page mini event-list renders restriction history | POOLV-04 | Frontend in sibling repo, read-only list off the HLTH-03 endpoint | Open an account with logged events → list shows newest-first events with type/source/restricted_until and activity-slice summary |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references (test_restriction_audit.py, test_pool_health.py)
- [ ] No watch-mode flags
- [ ] Feedback latency < 90s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
