---
phase: 14
slug: reliable-contact-resolution
status: draft
nyquist_compliant: false
wave_0_complete: false
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
| TBD | TBD | TBD | RESV-XX | — | TBD | unit | TBD | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky — planner fills this map from RESEARCH.md §Validation Architecture.*

---

## Wave 0 Requirements

- [ ] Test stubs for RESV-01 (health-probe ≥2-consecutive-miss detection logic)
- [ ] Test stubs for RESV-05 (worker selection skips restricted/paused checkers)
- [ ] Test stubs for RESV-06/D-09 (confidence/source finalization rule)
- [ ] Shared fixtures for checker/senders/contacts_cache (extend existing conftest)

*Planner to refine against RESEARCH.md §Validation Architecture.*

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
