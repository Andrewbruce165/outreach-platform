---
phase: 15
slug: account-warmup-via-inter-account-ai-chat
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-06-29
---

# Phase 15 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Derived from 15-RESEARCH.md §Validation Architecture. The isolation guarantees
> (WARM-01/02/04) are the core risk — build their tests RED in Wave 0 first.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest + pytest-asyncio (`pytestmark = pytest.mark.asyncio`) |
| **Config file** | `tests/conftest.py` (DSN guard at 46-77; `_setup_database`) |
| **Quick run command** | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_warmup_isolation.py tests/test_warmup_router.py tests/test_warmup_worker.py -x` |
| **Full suite command** | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest` |
| **Estimated runtime** | quick ~20-40s; full suite ~3-5 min (786 tests GREEN baseline 2026-06-29) |

**Never** run `docker compose run --rm api pytest` without the test-overlay — DSN points at prod and conftest will `DROP SCHEMA` (CLAUDE.md guard; MEMORY `feedback_pytest_drop_schema_prod`).

---

## Sampling Rate

- **After every task commit:** Run the quick command above (warmup test trio, `-x`).
- **After every plan wave:** Run the full suite.
- **Before `/gsd:verify-work`:** Full suite must be green.
- **Max feedback latency:** ~40 seconds (quick command).

---

## Per-Task Verification Map

| Req ID | Behavior | Test Type | Automated Command | File Exists |
|--------|----------|-----------|-------------------|-------------|
| WARM-01 | Internal sender tg_id detected from workspace senders (not pool, not phone) | unit/integration | `pytest tests/test_warmup_isolation.py::test_internal_detected_by_workspace_telegram_id -x` | ❌ W0 |
| WARM-02 | Internal inbound → no `conversations`/`messages` row, no `schedule_ai_response` | integration | `pytest tests/test_warmup_isolation.py::test_internal_inbound_no_dbwrite_no_ai -x` | ❌ W0 |
| WARM-02 | Analytics `_EXCLUDE_INTERNAL_CLAUSE` still excludes internal | integration | `pytest tests/test_phase5_analytics.py::test_internal_warmup_conversation_excluded -x` | ✅ keep green |
| WARM-04 | Source-introspection guard: short-circuit wired in both handlers | unit | `pytest tests/test_warmup_isolation.py::test_shortcircuit_wired -x` | ❌ W0 |
| WARM-05 | Router workspace-scoped: cross-workspace pool/sessions invisible | integration | `pytest tests/test_warmup_router.py::test_pool_workspace_scoped -x` | ❌ W0 |
| WARM-05 | Existing response shapes preserved | integration | `pytest tests/test_warmup_router.py::test_response_shapes_preserved -x` | ❌ W0 |
| WARM-06 | Disabled workspace produces no new sessions | integration | `pytest tests/test_warmup_worker.py::test_disabled_workspace_skipped -x` | ❌ W0 |
| WARM-10 | Empty settings resolve to 24 RU topics + default prompt | unit | `pytest tests/test_warmup_worker.py::test_content_defaults_when_empty -x` | ❌ W0 |
| WARM-14 | Restricted/frozen/future-restricted sender excluded from pool selection | integration | `pytest tests/test_warmup_worker.py::test_restricted_sender_excluded -x` | ❌ W0 |
| mig 037 | Idempotent re-apply | integration | runs in `_setup_database` round-trip (migration-test DB) | ✅ harness |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky — planner assigns task IDs.*

---

## Wave 0 Requirements

- [ ] `tests/test_warmup_isolation.py` — WARM-01, WARM-02, WARM-04 (core risk; build RED first)
- [ ] `tests/test_warmup_router.py` — WARM-05 (workspace scoping + response-shape preservation)
- [ ] `tests/test_warmup_worker.py` — WARM-06, WARM-10, WARM-14
- [ ] Shared fixtures: two-senders-same-workspace factory + fake-inbound-event helper (patch `get_me`/`get_sender`/`schedule_ai_response` with `AsyncMock`). Reuse `async_db_session` + workspace/sender factories already in `conftest.py`.
- Framework install: none — pytest infra exists.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| UI tab renders master toggle + per-account status with restriction reason | WARM-07/WARM-11 | Frontend in separate Lovable repo, generated from openapi.json | After API deploy, open warmup tab on `aimly.agsventurelab.com`: toggle warmup on/off, confirm per-account rows show level/sent_today/restriction reason |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 40s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
