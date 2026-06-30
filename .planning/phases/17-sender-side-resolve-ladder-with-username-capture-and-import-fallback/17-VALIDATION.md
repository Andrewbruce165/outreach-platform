---
phase: 17
slug: sender-side-resolve-ladder-with-username-capture-and-import-fallback
status: planned
nyquist_compliant: true
wave_0_complete: false  # Wave 0 = plan 17-01 (RED scaffold), executes first
created: 2026-06-30
---

# Phase 17 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Seeded from `17-RESEARCH.md` § Validation Architecture. Planner fills Task IDs in the Per-Task map during planning.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest + pytest-asyncio (`asyncio_mode = "auto"`, session-scoped loop) |
| **Config file** | `pyproject.toml` `[tool.pytest.ini_options]` |
| **Quick run command** | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_checker.py tests/test_send.py tests/test_contact_check_worker.py -x` |
| **Full suite command** | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest` |
| **Estimated runtime** | ~60–120 seconds (full suite ~837 collected as of Phase 16) |

**CRITICAL (CLAUDE.md):** NEVER run `docker compose run --rm api pytest` without the test-overlay — the conftest guard blocks it, but the canonical path is the overlay (ephemeral `db-test` in tmpfs). NEVER `down -v` afterward (wipes prod `postgres_data`; recover from `/root/backups/tg-outreach/`).

---

## Sampling Rate

- **After every task commit:** Run the quick run command (checker + send + worker tests, `-x`).
- **After every plan wave:** Run the full suite command (must stay green).
- **Before `/gsd:verify-work`:** Full suite must be green. Baseline is GREEN (memory `project-test-baseline-red` — was 81 failing, now green; `TEST_EXIT==0` trustworthy).
- **Max feedback latency:** ~120 seconds

---

## Per-Task Verification Map

> Task IDs assigned by planner. Requirement → test mapping from research is authoritative.

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| T1 | 17-02 | 2 | SRLD-01 | unit | `pytest tests/test_checker.py -k username_capture -x` | ❌ W0 | 🟥 RED (built in 17-01) |
| T1 | 17-02 | 2 | SRLD-02 | integration | `pytest tests/test_contact_check_worker.py -k captured_username -x` | ❌ W0 | 🟥 RED (built in 17-01) |
| T2 | 17-03 | 2 | SRLD-03 | unit | `pytest tests/test_send.py -k resolve_ladder -x` | ❌ W0 | 🟥 RED (built in 17-01) |
| T2 | 17-03 | 2 | SRLD-04 | unit | `pytest tests/test_send.py -k import_gate -x` | ❌ W0 | 🟥 RED (built in 17-01) |
| T2 | 17-03 | 2 | SRLD-05 | unit + grep | `pytest tests/test_send.py -k lazy_import -x` + `grep -n "DeleteContacts" app/services/telegram.py` (expect 0 in send path) | ❌ W0 | 🟥 RED (built in 17-01) |
| T3 | 17-03 | 2 | SRLD-06 | unit | `pytest tests/test_send.py -k stale_username_fallthrough -x` | ❌ W0 | 🟥 RED (built in 17-01) |
| T2/T3 | 17-02/17-03 | 2 | SRLD-07 | integration | `pytest tests/test_checker.py tests/test_send.py -k confidence_gated_cache -x` | ❌ W0 | 🟥 RED (built in 17-01) |
| T1/T2 | 17-04 | 3 | SRLD-08 | integration | `pytest tests/test_restriction_audit.py -k blocked -x` + `pytest tests/test_send.py -k user_blocked -x` | ❌ W0 | 🟥 RED (built in 17-01) |
| T3 | 17-04 | 3 | SRLD-09 | manual/grep | `grep -n "гипотеза\|hypothesis" /root/CLAUDE.md` (doc task) | manual | 🟥 RED (built in 17-01) |
| — | n/a | n/a | mig 044 (if column added) | integration | `pytest tests/ -k migration -x` (mirror `test_migration_032` pattern) | ❌ W0 | ✅ N/A — Phase 17 adds 0 migrations (all columns reused) |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_checker.py` — extend for SRLD-01 (username capture) + SRLD-07 (confidence-gated `_lookup_cache`). Fixture `mock_telethon_client` already exists (conftest, Plan 14-01) with `.set_response()` / `.calls`.
- [ ] `tests/test_send.py` — SRLD-03/04/05/06/07 (resolve ladder, import gate, lazy import, stale fall-through, gated read on sender). Existing file uses `pytestmark = pytest.mark.asyncio` + `async_client` / `async_db_session` fixtures.
- [ ] `tests/test_contact_check_worker.py` — SRLD-02 (captured username persisted to `tg_username_resolved`).
- [ ] `tests/test_restriction_audit.py` — SRLD-08 (`event_type='blocked'` insert, no CHECK violation, block-rate aggregate).
- [ ] Migration round-trip test (only if mig 044 adds a column) — mirror `tests/test_migration_032.py`.
- [ ] Framework install: none — pytest / pytest-asyncio already configured.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Throttle-burst onset (~47–49 resolves) | D-05 (SRLD-05) | Live Telegram server-side mechanism — not unit-testable | Structural proxy only: assert one-import-per-send, grep that `queue.py` intervals are untouched, assert no batch import loop. No live test in CI. |
| CLAUDE.md country-wording softened | SRLD-09 | Doc edit in `/root/CLAUDE.md` | `grep -n "гипотеза\|hypothesis" /root/CLAUDE.md` confirms the section is reframed as hypothesis, not fact. |
| Report-rate (recipient reports) | D-15 | Telegram never exposes report counts | Explicitly NOT trackable (design-doc truth). Only block-on-send + `sender_restriction_events` aggregate proxy is observable/testable. |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 120s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** planned 2026-06-30 — Wave 0 (17-01) precedes all behavior changes; all SRLD reqs have an automated -k target; no migration round-trip needed (0 columns added).
