---
phase: 13
slug: even-pacing-across-sending-window-smooth-new-dialog-distribu
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-06-26
---

# Phase 13 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (async, via test-overlay) |
| **Config file** | tests/conftest.py + docker-compose.test.yml |
| **Quick run command** | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_queue_pacing.py -q` |
| **Full suite command** | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest -q` |
| **Estimated runtime** | ~TBD (planner to confirm; queue suite is fast) |

> NEVER run `docker compose run --rm api pytest` without the test overlay — conftest guard DROP SCHEMA hits prod. Test-overlay db-test is ephemeral (tmpfs).

---

## Sampling Rate

- **After every task commit:** Run quick run command
- **After every plan wave:** Run full suite command
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** TBD (planner)

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| TBD | TBD | TBD | PACE-XX | — | N/A | unit | TBD | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky — planner fills this map from RESEARCH §Validation Architecture.*

---

## Wave 0 Requirements

- [ ] `tests/test_queue_pacing.py` — stubs for PACE-* (closest analog: `tests/test_queue_new_dialog_limit.py`)
- [ ] Injectable `now` into the elapsed-fraction helper so time math is unit-testable (no freezegun in project)

*Planner to finalize against RESEARCH §Validation Architecture.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| TBD | TBD | TBD | TBD |

*Planner to finalize; acceptance "new dialogs spread across window, not bunched" may need a longer-window integration assertion vs manual observation.*

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < target
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
