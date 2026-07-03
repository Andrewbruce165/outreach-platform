---
phase: 20
slug: account-profile-management
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-07-03
---

# Phase 20 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x + pytest-asyncio (`asyncio_mode=auto`, session-scoped loop) |
| **Config file** | `pyproject.toml` (`[tool.pytest.ini_options]`) |
| **Quick run command** | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_senders.py tests/test_account_profile.py -x` |
| **Full suite command** | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest` |
| **Estimated runtime** | ~60-90 seconds (quick) / full suite per existing baseline (~896+ collected) |

**CRITICAL (CLAUDE.md + memory `feedback_pytest_drop_schema_prod`):** tests run **ONLY** through the test-overlay. NEVER `docker compose run --rm api pytest` without `-f docker-compose.test.yml` (DATABASE_URL → prod, conftest DROP SCHEMA). NEVER `down -v` (wipes prod volume).

**Mocking pattern (verified in `tests/test_onboarding.py`):** Telethon mocked via `monkeypatch.setattr` on the client factory with `AsyncMock` clients. For profile tests, mock `TelegramService` methods (or the `client(...)` request dispatch + `edit_2fa`/`upload_file`/`download_profile_photo`) with `AsyncMock`; assert on the TL request type dispatched (Phase 17 request-type-introspection style). API-level integration tests bootstrap a workspace via JWT (`_create_workspace_via_jwt`) and insert senders via `_insert_sender_raw` (reuse from `test_senders.py`).

---

## Sampling Rate

- **After every task commit:** Run `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_account_profile.py -x`
- **After every plan wave:** Run `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_senders.py tests/test_account_profile.py tests/test_onboarding.py`
- **Before `/gsd:verify-work`:** Full suite must be green (baseline is GREEN per memory `project-test-baseline-red`)
- **Max feedback latency:** ~90 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 20-01-01 | 01 | 0 | PROF-01 | — | new `senders` columns present; ORM/migration parity; raw INSERT omitting JSONB succeeds via `server_default` | integration | `pytest tests/test_account_profile.py::test_profile_columns_defaults -x` | ❌ W0 | ⬜ pending |
| 20-0x-0x | TBD | TBD | PROF-02 | — | name/bio update dispatches `UpdateProfileRequest`; `AboutTooLongError` → 400 | unit | `pytest tests/test_account_profile.py::test_update_name_bio -x` | ❌ W0 | ⬜ pending |
| 20-0x-0x | TBD | TBD | PROF-03 | — | username pre-check + set; taken → 400, not-modified → ok; 1h hard-block enforced | unit+integration | `pytest tests/test_account_profile.py::test_username -x` | ❌ W0 | ⬜ pending |
| 20-0x-0x | TBD | TBD | PROF-04 | — | photo upload dispatches `upload_file`+`UploadProfilePhotoRequest`; delete path; 1h hard-block; size/mime validation | integration | `pytest tests/test_account_profile.py::test_photo -x` | ❌ W0 | ⬜ pending |
| 20-0x-0x | TBD | TBD | PROF-05 | — | password set/change via `edit_2fa`; wrong password → PASSWORD_INVALID; email-change start → EMAIL_CONFIRMATION_SENT; confirm step | unit | `pytest tests/test_account_profile.py::test_2fa -x` | ❌ W0 | ⬜ pending |
| 20-0x-0x | TBD | TBD | PROF-06 | — | resync updates cached fields from `GetFullUserRequest`/`get_me`/`download_profile_photo` | integration | `pytest tests/test_account_profile.py::test_resync -x` | ❌ W0 | ⬜ pending |
| 20-0x-0x | TBD | TBD | PROF-07 | — | photo-serve endpoint returns bytes+mime, requires auth, workspace-scoped 404 on mismatch | integration | `pytest tests/test_account_profile.py::test_photo_serve_auth -x` | ❌ W0 | ⬜ pending |
| 20-0x-0x | TBD | TBD | PROF-08 | — | onboarding finalize populates profile cache (username at minimum) | integration | `pytest tests/test_onboarding.py::test_finalize_caches_profile -x` | extend existing | ⬜ pending |
| 20-0x-0x | TBD | TBD | D-08 | — | username/photo change <1h ago → hard block (409/422), save disabled equivalent | unit | `pytest tests/test_account_profile.py::test_cooldown_block -x` | ❌ W0 | ⬜ pending |
| 20-0x-0x | TBD | TBD | D-09 | — | warmup / <7-day account → warning surfaced, NOT blocked | unit | `pytest tests/test_account_profile.py::test_warmup_advisory_not_blocking -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

*Task IDs and wave numbers are TBD pending gsd-planner's actual task breakdown — this table maps proposed requirements to tests; the planner fills in concrete task IDs/waves in PLAN.md and this map should be reconciled at that point.*

---

## Wave 0 Requirements

- [ ] `tests/test_account_profile.py` — new file covering PROF-01..08 + D-08/D-09 guardrails (RED scaffold, deferred in-body imports to keep `--collect-only` clean, per Phase 13/16/17/18 convention)
- [ ] Extend `tests/test_onboarding.py` — finalize caches profile fields (PROF-08)
- [ ] Mock helper for Telethon profile ops (AsyncMock `client(...)` dispatch + `edit_2fa`/`upload_file`/`download_profile_photo`) — model on `tests/test_onboarding.py::_make_mock_client`

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Recovery-email two-request confirm flow end-to-end against live Telegram | PROF-05 | Research flagged this as MEDIUM-LOW confidence — mechanism verified against Telethon signatures but not executed against a live Telegram account; Telegram's own email-confirmation code delivery can't be mocked meaningfully for a real-world check | Use a real test Telegram account with 2FA already set; call the start endpoint with a new recovery email, confirm `EmailUnconfirmedError`/pending state is surfaced correctly, retrieve the real confirmation code from the test email inbox, call the confirm endpoint, verify Telegram reflects the new recovery email via native client |
| Photo visual correctness after normalization | PROF-04/PROF-06 | Automated tests can assert bytes were stored/served but not that the image renders correctly / isn't corrupted after Telegram's own normalization round-trip | Upload a test photo via the API, then view the account card avatar and the full profile edit view in the actual frontend; confirm image displays correctly, matches what native Telegram client shows |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 90s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
