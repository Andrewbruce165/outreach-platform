---
phase: 23
slug: edit-and-delete-for-everyone-of-sent-messages-plus-file-sending-from-inbox-ui
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-07-07
---

# Phase 23 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (async, httpx AsyncClient + ephemeral postgres) |
| **Config file** | `docker-compose.test.yml` (test-overlay) + `tests/conftest.py` |
| **Quick run command** | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest -k conversations` |
| **Full suite command** | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest` |
| **Estimated runtime** | ~TBD by planner (full suite: minutes) |

> ⚠️ NEVER run `docker compose run --rm api pytest` without the test-overlay — DATABASE_URL points at prod and conftest DROP SCHEMA wipes it. Always the two-file overlay above.

---

## Sampling Rate

- **After every task commit:** Run quick command (`-k conversations` or the relevant `-k` selector)
- **After every plan wave:** Run full suite command
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** TBD by planner

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| _to be filled by planner_ | | | | | | | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/conftest.py` — **teach the hardcoded migration list to apply new migration `053`** (conftest applies an explicit list, NOT a glob — omitting 053 → every inbox integration test hits `UndefinedColumn`). Flagged by research as the single most important Wave-0 task.
- [ ] Test stubs for delete / edit / send-file / incoming-media capabilities (mock TelegramService at the client-per-op boundary).

*If none: "Existing infrastructure covers all phase requirements."*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| End-to-end revoke/edit/file-send against a live Telegram account | delete/edit/send-file | Requires a real MTProto session + real peer; final proof only | Live-smoke: from inbox, delete an outbound msg (verify gone both sides), edit an outbound text (verify "(изменено)"), send a photo (verify arrives as photo not document), receive a file from contact (verify bubble + on-demand download) |
| Exact edit-time-window value | MESSAGE_EDIT_TOO_OLD | Server-controlled by Telegram, not code | Live-smoke: edit a message older than the window → assert `MESSAGE_EDIT_TOO_OLD` |

*If none: "All phase behaviors have automated verification."*

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references (esp. conftest migration-053 apply)
- [ ] No watch-mode flags
- [ ] Feedback latency threshold set by planner
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
