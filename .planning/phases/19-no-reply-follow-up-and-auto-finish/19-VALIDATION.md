---
phase: 19
slug: no-reply-follow-up-and-auto-finish
status: draft
nyquist_compliant: false
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
| (filled by planner) | | | | | | | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- (filled by planner from RESEARCH.md Validation Architecture)

*If none: "Existing infrastructure covers all phase requirements."*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| (filled by planner) | | | |

*If none: "All phase behaviors have automated verification."*

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 300s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
