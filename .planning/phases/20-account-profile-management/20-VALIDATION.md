---
phase: 20
slug: account-profile-management
status: planned
nyquist_compliant: true
wave_0_complete: false
created: 2026-07-03
reconciled: 2026-07-03
---

# Phase 20 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Reconciled with the 5 committed plans (20-01..20-05) on 2026-07-03 — task IDs/waves are now concrete (Phase 19 precedent).

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

*(All pytest commands run through the test-overlay prefix `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api`.)*

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 20-01-T1 | 01 | 1 | PROF-01 | — | new `senders` columns present; ORM/migration parity; raw INSERT omitting JSONB succeeds via `server_default` | integration | `pytest tests/test_account_profile.py::test_profile_columns_defaults -x` | ❌→W0 (T3, same plan) | ⬜ pending |
| 20-01-T2 | 01 | 1 | PROF-01 | — | ProfileUpdate/TwoFA/RecoveryEmail/ProfileWarningItem/ProfileUpdateResponse schemas import cleanly; pre-existing rate-limit `WarningItem` untouched | schema | `pytest tests/test_account_profile.py -x --collect-only` | ❌→W0 | ⬜ pending |
| 20-01-T3 | 01 | 1 | all (scaffold) | — | RED scaffold collects clean (deferred in-body imports); `test_profile_columns_defaults` GREEN, 8 behavioural tests + PROF-08 RED | scaffold | `pytest tests/test_account_profile.py --collect-only && pytest tests/test_account_profile.py::test_profile_columns_defaults -x` | creates it | ⬜ pending |
| 20-02-T1 | 02 | 2 | PROF-02, PROF-03 | — | name/bio update dispatches `UpdateProfileRequest` (AboutTooLong → 400); username pre-check + set (taken → 400, not-modified → ok) | unit | `pytest tests/test_account_profile.py::test_update_name_bio tests/test_account_profile.py::test_username -x` | ✅ (after 20-01) | ⬜ pending |
| 20-02-T2 | 02 | 2 | PROF-02, PROF-03, D-08, D-09 | — | PATCH /profile + /username-check endpoints; username <1h → 409 TOO_FREQUENT hard block; warmup/<7d → ProfileWarningItem advisory, NOT blocked | api | `pytest tests/test_account_profile.py::test_update_name_bio tests/test_account_profile.py::test_username tests/test_account_profile.py::test_cooldown_block tests/test_account_profile.py::test_warmup_advisory_not_blocking -x` | ✅ | ⬜ pending |
| 20-02-T3 | 02 | 2 | PROF-08 | — | onboarding finalize populates profile cache (tg_username) on create + reauth-upsert paths | integration | `pytest tests/test_onboarding.py::test_finalize_caches_profile -x` | ✅ | ⬜ pending |
| 20-03-T1 | 03 | 3 | PROF-04, PROF-06 | — | set_profile_photo dispatches upload_file+UploadProfilePhotoRequest (caches normalized avatar); delete fetches fresh file_reference; resync pulls get_me/GetFullUser/download_profile_photo | unit | `pytest tests/test_account_profile.py::test_photo tests/test_account_profile.py::test_resync -x` | ✅ | ⬜ pending |
| 20-03-T2 | 03 | 3 | PROF-04, PROF-06, PROF-07 | — | photo upload/delete/serve + resync endpoints; size/mime validation (413/422); photo 1h hard block; serve requires auth, workspace-scoped 404 | integration | `pytest tests/test_account_profile.py::test_photo tests/test_account_profile.py::test_resync tests/test_account_profile.py::test_photo_serve_auth -x` | ✅ | ⬜ pending |
| 20-04-T1 | 04 | 4 | PROF-05 | — | edit_2fa password path (no email kwarg — Pitfall 2); recovery-email raw two-request flow pivots on EmailUnconfirmedError; wrong pwd → PASSWORD_INVALID | unit | `pytest tests/test_account_profile.py::test_2fa -x` | ✅ | ⬜ pending |
| 20-04-T2 | 04 | 4 | PROF-05 | — | POST /2fa + /2fa/recovery-email + /confirm endpoints; TOO_FRESH → 409, FLOOD_WAIT → 429; no 2FA field ever written to DB (D-03) | api | `pytest tests/test_account_profile.py::test_2fa -x` | ✅ | ⬜ pending |
| 20-05-T1 | 05 | 5 | PROF-09 | — | regenerated openapi.json carries all 7 Phase-20 paths + SenderResponse profile fields | contract | `jq -e '.paths \| has("/api/v1/senders/{slug}/profile") and has("/api/v1/senders/{slug}/2fa/recovery-email/confirm") and has("/api/v1/senders/{slug}/photo")' lovable-handoff/openapi.json` | n/a | ⬜ pending |
| 20-05-T2 | 05 | 5 | PROF-09 | — | accounts.tsx: enriched row + kebab (Изменить профиль / Обновить профиль) + two-section modal + two-step recovery email; typecheck clean | frontend | `grep -q "Изменить профиль" && grep -q "Обновить профиль" && grep -q "recovery-email" src/routes/_authenticated/accounts.tsx` (sibling repo) + typecheck | n/a | ⬜ pending |
| 20-05-T3 | 05 | 5 | PROF-09 (+ manual-only items) | — | human-verify gate: 8-step visual + live recovery-email round-trip | human-verify | manual (see Manual-Only Verifications) | n/a | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_account_profile.py` — new file covering PROF-01..07 + D-08/D-09 guardrails (RED scaffold, deferred in-body imports to keep `--collect-only` clean, per Phase 13/16/17/18 convention) — **created by 20-01-T3**
- [ ] Extend `tests/test_onboarding.py` — finalize caches profile fields (PROF-08, `test_finalize_caches_profile`) — **created by 20-01-T3**
- [ ] Mock helper for Telethon profile ops (AsyncMock `client(...)` dispatch + `edit_2fa`/`upload_file`/`download_profile_photo`) — model on `tests/test_onboarding.py::_make_mock_client` — **created by 20-01-T3**

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Recovery-email two-request confirm flow end-to-end against live Telegram | PROF-05 | Research flagged this as MEDIUM-LOW confidence — mechanism verified against Telethon signatures but not executed against a live Telegram account; Telegram's own email-confirmation code delivery can't be mocked meaningfully | Covered by **20-05-T3** step 8: real test account with 2FA set → start endpoint with new recovery email → confirm EMAIL_CONFIRMATION_SENT state → retrieve the real code from the inbox → confirm endpoint → verify Telegram reflects the new recovery email via native client |
| Photo visual correctness after normalization | PROF-04/PROF-06 | Automated tests can assert bytes were stored/served but not that the image renders correctly / isn't corrupted after Telegram's own normalization round-trip | Covered by **20-05-T3** steps 1+4: upload a test photo via the UI, then view the account row avatar and the profile edit view; confirm image displays correctly and matches what the native Telegram client shows |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies (20-01-T1/T2 depend on the 20-01-T3 scaffold created in the same plan; 20-05-T3 is the declared human-verify gate)
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references (single scaffold task 20-01-T3 creates every referenced test)
- [x] No watch-mode flags
- [x] Feedback latency < 90s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** reconciled with plans 20-01..20-05 (2026-07-03); `wave_0_complete` flips to true when 20-01 executes.
