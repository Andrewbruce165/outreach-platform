---
phase: 23
slug: edit-and-delete-for-everyone-of-sent-messages-plus-file-sending-from-inbox-ui
status: planned
nyquist_compliant: true
wave_0_complete: false
created: 2026-07-07
updated: 2026-07-07
---

# Phase 23 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (async, httpx AsyncClient + ephemeral postgres) |
| **Config file** | `docker-compose.test.yml` (test-overlay) + `tests/conftest.py` |
| **Quick run command** | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_phase23_inbox_mutations.py -x` |
| **Full suite command** | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest` |
| **Estimated runtime** | quick file: <30s; full suite: ~minutes |

> ⚠️ NEVER run `docker compose run --rm api pytest` without the test-overlay — DATABASE_URL points at prod and conftest DROP SCHEMA wipes it. Always the two-file overlay above.

---

## Sampling Rate

- **After every task commit:** quick command with the relevant `-k` selector (per-task map below)
- **After every plan wave:** `pytest tests/test_phase23_inbox_mutations.py tests/test_phase5_inbox*.py`
- **Before `/gsd:verify-work`:** full suite green (run targeted subset + clean-tree diff — full-suite has known order-dependent flakiness per STATE.md memory)
- **Max feedback latency:** ≤1 task (no 3 consecutive tasks without an automated verify)

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 23-01-T1 migration 053 | 01 | 1 | INBM-08 | structural | `grep structural check on migrations/053_*.sql` | n/a | ⬜ pending |
| 23-01-T2 conftest + RED scaffold | 01 | 1 | INBM-08 (+01..07 scaffold) | harness | `pytest ... --collect-only -q && pytest ... -k schema -x` | ✅ created here | ⬜ pending |
| 23-01-T3 schemas | 01 | 1 | INBM-08 | smoke | `python -c "from app.schemas import MessageResponse, EditMessageRequest, SendFileFromUIResponse"` | ✅ | ⬜ pending |
| 23-02-T1 edit/delete methods | 02 | 1 | INBM-01/02/06 | smoke | `python -c "hasattr checks on edit/delete/_resolve_peer"` | ✅ | ⬜ pending |
| 23-02-T2 send_file/download methods | 02 | 1 | INBM-03/05 | smoke | `python -c "hasattr checks on send_file/download"` | ✅ | ⬜ pending |
| 23-03-T1 error helper + gate + SELECT | 03 | 2 | INBM-06/07 | integration | `pytest tests/test_phase23_inbox_mutations.py -k messages_select tests/test_phase5_inbox_send_takeover.py -x` | ✅ (23-01) | ⬜ pending |
| 23-03-T2 PATCH edit | 03 | 2 | INBM-02 | integration | `pytest tests/test_phase23_inbox_mutations.py -k edit -x` | ✅ (23-01) | ⬜ pending |
| 23-03-T3 DELETE revoke | 03 | 2 | INBM-01 | integration | `pytest tests/test_phase23_inbox_mutations.py -k delete -x` | ✅ (23-01) | ⬜ pending |
| 23-04-T1 save_message params | 04 | 2 | INBM-04 | unit | `pytest tests/test_phase23_inbox_mutations.py -k save_message_persists -x` | ✅ (23-01) | ⬜ pending |
| 23-04-T2 media classify | 04 | 2 | INBM-04 | integration | `pytest tests/test_phase23_inbox_mutations.py -k incoming_media -x` | ✅ (23-01) | ⬜ pending |
| 23-05-T1 send-file endpoint | 05 | 3 | INBM-03/07 | integration | `pytest tests/test_phase23_inbox_mutations.py -k send_file tests/test_phase5_inbox_send_takeover.py -x` | ✅ (23-01) | ⬜ pending |
| 23-05-T2 download endpoint | 05 | 3 | INBM-05/07 | integration | `pytest tests/test_phase23_inbox_mutations.py -k download -x` | ✅ (23-01) | ⬜ pending |
| 23-06-T1 handoff regen | 06 | 4 | INBM-09 | smoke | `python3 -c "openapi.json paths assertion → openapi ok"` | ✅ (regen) | ⬜ pending |
| 23-06-T2 live-smoke | 06 | 4 | INBM-01..05 | MANUAL | human live-smoke (no automated command) | n/a | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/conftest.py` — teach the hardcoded migration list to apply new migration `053` (conftest applies an explicit list, NOT a glob — omitting 053 → every inbox integration test hits `UndefinedColumn`). **Owned by plan 23-01 Task 2.** Single most important Wave-0 task.
- [ ] `tests/test_phase23_inbox_mutations.py` — RED stubs for schema / messages_select (widened GET /messages) / save_message_persists (listener unit) / edit / delete / send-file / incoming-media / download (mock TelegramService at the client-per-op boundary, `raising=False`). **Owned by plan 23-01 Task 2.**

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| End-to-end revoke/edit/file-send against a live Telegram account | INBM-01/02/03/05 | Requires a real MTProto session + real peer; final proof only | Plan 23-06 Task 2 live-smoke: delete an outbound msg (gone both sides), edit an outbound text (verify «изменено»), send a photo (arrives as photo not document), receive a file + on-demand download |
| Exact edit-time-window value | INBM-02 (MESSAGE_EDIT_TOO_OLD) | Server-controlled by Telegram, not code | Live-smoke: edit a message older than the window → assert `MESSAGE_EDIT_TOO_OLD` (or note the window is still open) |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or a Wave 0 dependency (23-06 Task 2 is the single sanctioned MANUAL live-smoke with automated coverage in the test file)
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references (esp. conftest migration-053 apply — plan 23-01 Task 2)
- [x] No watch-mode flags
- [x] Feedback latency threshold set (≤1 task)
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** planned (turns to green as waves execute)
