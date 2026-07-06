---
phase: 21
slug: bulk-telegram-account-import-via-session-json-upload-in-ui
status: draft
nyquist_compliant: true
wave_0_complete: false
created: 2026-07-06
---

# Phase 21 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x (async via pytest-asyncio) |
| **Config file** | pyproject.toml / tests/conftest.py |
| **Quick run command** | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_account_import.py tests/test_account_import_worker.py -q` |
| **Full suite command** | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest` |
| **Estimated runtime** | ~90 seconds (full suite); ~15s (import-only quick run) |

> **NEVER** run `docker compose run --rm api pytest` without the test-overlay — conftest guard will DROP SCHEMA on prod. See CLAUDE.md and memory `feedback_pytest_drop_schema_prod.md`.

---

## Sampling Rate

- **After every task commit:** Run quick run command (import-only files)
- **After every plan wave:** Run full suite command
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 90 seconds

---

## Per-Task Verification Map

> Filled by the planner from RESEARCH.md Validation Architecture. Every task must map to an automated command OR a Wave 0 test-file dependency.

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 21-01-1 | 01 | 1 | IMPT-08 | schema | `pytest tests/test_account_import.py --collect-only -q` | ❌ W0 | ⬜ pending |
| 21-01-2 | 01 | 1 | scaffold (all IMPT) | scaffold | `pytest tests/test_account_import.py tests/test_account_import_worker.py --collect-only -q` | ❌ W0 | ⬜ pending |
| 21-02-1 | 02 | 2 | IMPT-04 | unit | `pytest tests/test_account_import.py::test_fingerprint_override_and_strict_fallback -x -q` | ❌ W0 | ⬜ pending |
| 21-02-2 | 02 | 2 | IMPT-04 | unit/regression | `pytest tests/test_account_import.py -x -q` + targeted queue/warmup/checker/listener/contact_check/sender suites (automated-path threading + NULL regression + checker kwargs-capture) | ❌ W0 | ⬜ pending |
| 21-02-3 | 02 | 2 | IMPT-04, IMPT-10 | integration | `pytest tests/test_account_import.py -k "2fa or profile_method" -x -q` + sender/profile/2fa suites (profile-path method threading + 2FA autofill) | ❌ W0 | ⬜ pending |
| 21-03-1 | 03 | 2 | IMPT-01 | unit | `pytest tests/test_account_import.py::test_preview_pairing -x -q` | ❌ W0 | ⬜ pending |
| 21-03-2 | 03 | 2 | IMPT-01 | integration | `pytest tests/test_account_import.py -k preview -x -q` | ❌ W0 | ⬜ pending |
| 21-04-1 | 04 | 3 | IMPT-03, IMPT-05 | unit | `pytest tests/test_account_import.py -k "offline or twofa" -x -q` | ❌ W0 | ⬜ pending |
| 21-04-2 | 04 | 3 | IMPT-06, IMPT-07 | integration | `pytest tests/test_account_import.py -x -q` | ❌ W0 | ⬜ pending |
| 21-05-1 | 05 | 4 | IMPT-02 | integration | `pytest tests/test_account_import_worker.py -k "confirm or status" -x -q` | ❌ W0 | ⬜ pending |
| 21-05-2 | 05 | 4 | IMPT-02, IMPT-07 | integration | `pytest tests/test_account_import_worker.py -x -q` | ❌ W0 | ⬜ pending |
| 21-06-1 | 06 | 5 | IMPT-09 | contract | `grep -q "accounts/import/preview" lovable-handoff/openapi.json` | ✅ (regen) | ⬜ pending |
| 21-06-2 | 06 | 5 | IMPT-09 | typecheck | `cd ../aimly-tg-outreach && npx tsc --noEmit` | ➖ sibling | ⬜ pending |
| 21-06-3 | 06 | 5 | IMPT-09 (fingerprint) | manual | human-verify: mixed-batch import + reconnect-no-relogin | manual | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_account_import.py` — SQLite→StringSession conversion, vendor-JSON parse/validation, dedup-by-telegram_id, staging lifecycle (Telethon connect/get_me stubbed — no network)
- [ ] `tests/test_account_import_worker.py` — async import job: pair matching, per-account partial-failure result reporting, fingerprint persistence, proxy/2FA persistence (Telethon stubbed)
- [ ] shared fixtures in `tests/conftest.py` — reuse existing; add vendor-sample fixture (real `.json` + `.session` bytes) if not present

*Existing pytest infrastructure covers everything else; only the two import-specific files are new.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Imported session reconnects live without Telegram security-flag / forced re-login | IMPT (fingerprint) | Requires a real Telegram network connect with a real live vendor session; cannot be exercised in CI without leaking a live auth_key and hitting Telegram | After import, trigger a real connect for one imported sender in staging, confirm `get_me()` succeeds and no re-login/2FA prompt appears; check `sender_restriction_events` stays clean |
| Bulk UI upload pairs files and reports per-account results | IMPT (UI) | Frontend is a separate Lovable repo; visual/interaction flow | Upload a mixed batch (valid pair, orphan .json, orphan .session, duplicate) via UI; confirm per-account success/skip/error rows render |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 90s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
