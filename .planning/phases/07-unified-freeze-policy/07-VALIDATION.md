---
phase: 07
slug: unified-freeze-policy
status: ready
nyquist_compliant: true
wave_0_complete: true
created: 2026-06-23
---

# Phase 07 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest + pytest-asyncio (`asyncio_mode=auto` — async tests need no marker) |
| **Config file** | docker-compose.test.yml (test-overlay; ephemeral db-test in tmpfs) + pyproject.toml |
| **Quick run command** | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_spambot_selfcheck.py tests/test_sender_restriction.py tests/test_rotation_campaign.py -x` |
| **Full suite command** | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest` |
| **Estimated runtime** | ~3 files, seconds (DB-backed unit) |

⚠️ NEVER run bare `docker compose run --rm api pytest` — DATABASE_URL leaks to prod, conftest does DROP SCHEMA (2026-05-26 incident). Guard at tests/conftest.py:46-77; correct path is the overlay above.

---

## Sampling Rate

- **After every task commit:** Run quick run command (3 test files)
- **After every plan wave:** Run full suite command
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** seconds (no watch-mode, no network)

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 07-01-01 | 01 | 1 | FRZ-01, FRZ-02, FRZ-03 | T2 (reflag loop), T3 (frozen precedence) | Antispam signal pauses pending (status stays `pending`, scheduled_at +24h) + flags `spam_limited` (guarded `<> frozen`) + leaves `ai_enabled` True; self-check early-return preserved first | unit (DB-backed) | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_spambot_selfcheck.py tests/test_sender_restriction.py -x` | ✅ exists (assertions flipped in 07-01-03) | ⬜ pending |
| 07-01-02 | 01 | 1 | FRZ-04 | T1 (restricted sender re-armed) | Rotation candidate query excludes `restriction_status != 'none'` — no new cold contact on a limited account | unit (DB-backed) | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_rotation_campaign.py -x` | ✅ file exists (new test added in 07-01-03) | ⬜ pending |
| 07-01-03 | 01 | 1 | FRZ-01..05 | T1, T2, T3 | Cancel-path test encodes new contract (pending + spam_limited + ai_enabled True); rotation restricted-sender regression; FRZ-05 worker-skip asserted | unit (DB-backed) | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_spambot_selfcheck.py tests/test_rotation_campaign.py tests/test_sender_restriction.py -x` | ✅ test_spambot_selfcheck + test_sender_restriction exist; rotation test added | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

Threat refs: T1 = restricted sender re-armed for cold outreach; T2 = reconcile clear→reflag oscillation; T3 = frozen-vs-spam_limited silent downgrade. See 07-01-PLAN.md `<threat_model>`.

---

## Wave 0 Requirements

Existing test infrastructure (test-overlay + pytest) fully covers this phase — no new framework, no scaffold install. The one "Wave 0" subtlety is documented inline in Task 3 of 07-01-PLAN.md:

- **`tests/test_spambot_selfcheck.py::test_antispam_guard_cancels_when_no_selfcheck` currently asserts the OLD contract** (`q_status=='failed'`, `ai_enabled is False`). It WILL break under the new behaviour — this is expected and is rewritten (renamed to `test_antispam_guard_pauses_and_flags_when_no_selfcheck`) to the new contract (`pending` + `spam_limited` + `ai_enabled True`) within the same plan. Not a regression.
- New `test_rotation_skips_restricted_senders` in `tests/test_rotation_campaign.py` (FRZ-04) — cloned from `test_rotation_skips_inactive_senders`; factory accepts `restriction_status='spam_limited'` override directly (conftest.py:367-388).
- FRZ-05 worker-skip is ALREADY implemented (queue.py:401) and asserted by an EXISTING passing test `tests/test_sender_restriction.py::test_queue_pre_send_skips_restricted` — no production code, just include in the verify command.

All fixtures exist: `async_db_session` (conftest:187), `test_workspace` (349), `test_sender_factory` (359-388), `test_campaign_factory`, `attach_sender_to_campaign`; seed helper `_seed_queue_and_conversation` in test_spambot_selfcheck.py.

No MISSING `<automated>` references — every task has a runnable test command. No 3-consecutive-tasks-without-verify gap.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| End-to-end reconcile auto-resume against live @SpamBot | FRZ-02 (integration) | Requires a real Telegram session + @SpamBot round-trip; covered structurally by the unit assertion that paused items stay `pending` with `scheduled_at > NOW()` (the read-side contract the reconcile sweep consumes at listener.py:1402-1408) | Optional prod smoke: after a real antispam signal, confirm `SELECT restriction_status FROM senders` shows `spam_limited`, pending items are not `failed`, and the reconcile sweep clears + un-pauses once @SpamBot reports free. Not required for phase gate. |

All phase-internal behaviors have automated verification; only the live-Telegram round-trip is manual and is structurally covered by unit assertions.

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references (none MISSING; the one intentionally-breaking test is rewritten in-plan)
- [x] No watch-mode flags
- [x] Feedback latency < seconds (DB-backed unit, no network)
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** ready
