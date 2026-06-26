---
phase: 14
slug: reliable-contact-resolution
status: draft
nyquist_compliant: false
wave_0_complete: true
created: 2026-06-26
---

# Phase 14 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x (async via pytest-asyncio) |
| **Config file** | tests/conftest.py (test-overlay only — see CLAUDE.md) |
| **Quick run command** | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest -q` |
| **Full suite command** | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest` |
| **Estimated runtime** | ~TBD seconds (planner to fill) |

> **CRITICAL:** Never run `docker compose run --rm api pytest` without the test-overlay — DATABASE_URL points at prod and conftest will DROP SCHEMA. See memory `feedback_pytest_drop_schema_prod.md`.

---

## Sampling Rate

- **After every task commit:** Run quick run command
- **After every plan wave:** Run full suite command
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** TBD seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| T3 | 14-01 | 0 | RESV-05/D-11 | data-integrity | spam_limited checker NOT picked by `_tick`; contacts stay pending | unit | `pytest tests/test_contact_check_worker.py::test_selection_skips_restricted` | ✅ 14-01 | ❌ red (Wave 2 turns green) |
| T3 | 14-01 | 0 | RESV-05/D-11 | data-integrity | paused checker NOT picked by `_tick` | unit | `pytest tests/test_contact_check_worker.py::test_selection_skips_paused` | ✅ 14-01 | ❌ red (Wave 2) |
| T3 | 14-01 | 0 | RESV-04/D-08 | — | mobile (+79…) claimed before landline (+73…) regardless of created_at | unit | `pytest tests/test_contact_check_worker.py::test_mobile_first_order` | ✅ 14-01 | ❌ red (Wave 2) |
| T3 | 14-01 | 0 | RESV-06/D-09 | confidence-spoofing | clean-probe checker writes tg_confidence='high'+tg_resolved_by+tg_probe_state='clean' | unit | `pytest tests/test_contact_check_worker.py::test_confidence_written` | ✅ 14-01 | ❌ red (Wave 3) |
| T3 | 14-01 | 0 | RESV-01/D-05 | data-integrity | ≥2 consecutive control misses → spam_limited + audit row | unit | `pytest tests/test_checker_probe.py::test_two_misses_flags` | ✅ 14-01 | ❌ red (Wave 2) |
| T3 | 14-01 | 0 | RESV-01/D-05 | data-integrity | single miss is noise → no flag | unit | `pytest tests/test_checker_probe.py::test_single_miss_no_flag` | ✅ 14-01 | ❌ red (Wave 2) |
| T3 | 14-01 | 0 | RESV-01/D-07 | data-integrity | suspect batch: not_registered→pending, registered kept | unit | `pytest tests/test_checker_probe.py::test_suspect_rollback_keeps_registered` | ✅ 14-01 | ❌ red (Wave 3) |
| T3 | 14-01 | 0 | RESV-02/D-10 | self-inflicted DoS | per-tick resolves driven by + capped at contact_check_burst_cap | unit | `pytest tests/test_checker_cap.py::test_burst_cap` | ✅ 14-01 | ❌ red (Wave 2) |
| T3 | 14-01 | 0 | RESV-02/D-10 | self-inflicted DoS | daily cap durable across fresh worker instance (Pitfall 5) | unit | `pytest tests/test_checker_cap.py::test_daily_cap_durable` | ✅ 14-01 | ❌ red (Wave 2) |
| T3 | 14-01 | 0 | RESV-03 | — | two healthy checkers both eligible for rotation | unit | `pytest tests/test_checker_pool.py::test_rotation_picks_eligible` | ✅ 14-01 | ❌ red (Wave 2) |
| T3 | 14-01 | 0 | RESV-03/D-04 | data-integrity | N=1 resting checker → `_tick` resolves nothing, no false not_registered | integration | `pytest tests/test_checker_pool.py::test_rotation_n1_pauses` | ✅ 14-01 | ❌ red (Wave 2) |
| T3 | 14-01 | 0 | RESV-01/D-02 | account safety | ResolvePhone empty → importContacts fallback → DeleteContacts cleanup | unit (mock) | `pytest tests/test_checker.py::test_import_fallback_and_cleanup` | ✅ 14-01 | ❌ red (Wave 3) |
| T1 | 14-01 | 1 | RESV-06/D-09 | confidence-spoofing | Contact ORM exposes tg_confidence/tg_resolved_by/tg_probe_state (migration 034) | unit (import) | `python -c "from app.models import Contact; assert hasattr(Contact,'tg_confidence')"` | ✅ 14-01 | ✅ green |
| T2 | 14-01 | 1 | RESV-02/D-10 | self-inflicted DoS | five CONTACT_CHECK_* knobs readable with safe defaults | unit (config) | `python -c "from app.config import get_settings; assert get_settings().contact_check_burst_cap==30"` | ✅ 14-01 | ✅ green |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky. The Wave-0 RED rows are designed to flip green as Waves 2-3 land the worker/checker logic.*

> **Note (14-01):** the plan's `from app.config import settings` verify command was corrected to `from app.config import get_settings` — there is no module-level `settings` singleton in this repo; the codebase uses the `get_settings()` accessor (Rule 3 blocking fix). Assertions are unchanged.

---

## Wave 0 Requirements

- [x] Test stubs for RESV-01 (health-probe ≥2-consecutive-miss detection logic) — `tests/test_checker_probe.py`
- [x] Test stubs for RESV-05 (worker selection skips restricted/paused checkers) — `tests/test_contact_check_worker.py::test_selection_skips_*`
- [x] Test stubs for RESV-06/D-09 (confidence/source finalization rule) — `test_confidence_written` + `test_suspect_rollback_keeps_registered`
- [x] Test stubs for RESV-02/D-10 (burst + durable daily cap) — `tests/test_checker_cap.py`
- [x] Test stubs for RESV-03/D-04 (pool rotation + N=1 cooldown) — `tests/test_checker_pool.py`
- [x] Test stub for RESV-01/D-02 (importContacts fallback + cleanup) — `tests/test_checker.py`
- [x] Shared fixtures for checker/senders/contacts_cache (extend existing conftest) — reused existing + added `mock_telethon_client` fixture in conftest

*Wave-0 RED scaffold complete in 14-01: all stubs collect cleanly (`--collect-only` exit 0) and fail (genuinely RED) until Waves 2-3.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| 49 known-live control numbers resolve via a healthy checker | RESV-01/03 | Requires live Telegram session + real accounts | Run control-set probe against an activated checker; expect ~48/49 live |
| 14k contacts resolve end-to-end without hard shadow-ban | Success Criteria 3 | Multi-day live run | Monitor hit-rate + sender_restriction_events over the pool |

*If none: "All phase behaviors have automated verification."*

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < TBD s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
