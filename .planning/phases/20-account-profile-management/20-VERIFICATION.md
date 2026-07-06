---
phase: 20-account-profile-management
verified: 2026-07-06T15:49:42Z
status: passed
score: 9/9 must-haves verified
---

# Phase 20: Account Profile Management Verification Report

**Phase Goal:** Let workspace users edit their connected Telegram accounts' profile (name, bio, username, photo) and security (2FA password, recovery email) directly from the product, with per-field frequency guardrails to avoid Telegram anti-spam flags.
**Verified:** 2026-07-06T15:49:42Z
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | `senders` has cached profile columns (tg_username, tg_bio, tg_photo, tg_photo_mime, profile_field_changed_at) | ✓ VERIFIED | Migration `migrations/049_account_profile.sql` idempotent, applied to prod DB (`schema_migrations` row `049_account_profile` @ 2026-07-04 09:24:11); prod `\d senders` shows all 5 columns; ORM mirror in `app/models/__init__.py:130-138` with `server_default=text("'{}'::jsonb")` on the JSONB |
| 2 | User edits first/last name + bio and it's written to Telegram, warning-only, never blocked (PROF-02, D-07) | ✓ VERIFIED | `PATCH /senders/{slug}/profile` (senders.py:1070) dispatches `telegram_service.update_profile` → `UpdateProfileRequest`; name/bio not in `_HARD_BLOCK_FIELDS = {"username","photo"}` (line 227); `test_update_name_bio` GREEN |
| 3 | Username edit with availability pre-check; taken→error, current→no-op, <1h→409 hard block (PROF-03, D-08) | ✓ VERIFIED | `GET /username-check` (line 1027) + `PATCH /profile` username branch (line 1130-1135) call `check_username`/`update_username`; `_check_profile_cooldown` raises 409 TOO_FREQUENT for username <1h (line 251-...); `test_username`, `test_cooldown_block` GREEN |
| 4 | Newly onboarded account has tg_username cached without manual resync (PROF-08) | ✓ VERIFIED | `app/routers/onboarding.py` caches `getattr(me,"username",None)` on both create + re-auth-upsert paths; `test_finalize_caches_profile` GREEN |
| 5 | Photo upload becomes the Telegram profile photo, cached bytes served via auth-gated endpoint (PROF-04/07, D-11) | ✓ VERIFIED | `POST /senders/{slug}/photo` (line 1160) → `upload_profile_photo`; `GET /senders/{slug}/photo` (line 1275) returns `Response(content=sender.tg_photo, media_type=...)`, 404 opaque cross-workspace; `test_photo`, `test_photo_serve_auth` GREEN |
| 6 | Photo delete removes Telegram photo + clears cache; oversized/wrong-format rejected before any Telegram call; <1h hard block on upload (PROF-04, D-08) | ✓ VERIFIED | `DELETE /photo` (line 1231) clears `tg_photo`/`tg_photo_mime`; `MAX_PHOTO_BYTES`/`ALLOWED_PHOTO_MIME` validated before `_check_profile_cooldown`/Telegram call; `test_photo` (413/422 sub-assertions) GREEN |
| 7 | Manual resync re-fetches live username/bio/photo (and, post gap-fix, name) into cache, read-only no cooldown (PROF-06, D-12) | ✓ VERIFIED | `POST /senders/{slug}/resync` (line 1299) calls `fetch_profile`→`resync_profile`; composes `first_name`/`last_name` into `sender.name` (gap-closure `ed3960b`, verified present in running code); `test_resync`, `test_resync_updates_name` GREEN |
| 8 | 2FA password set/change (D-03/D-04); wrong current password → 400 never 500; recovery-email two-request flow; password never persisted | ✓ VERIFIED | `POST /2fa`, `/2fa/recovery-email`, `/2fa/recovery-email/confirm` (lines 1371/1411/1450) — grepped all three bodies: zero `sender.` assignments, no `db.commit()` (D-03 confirmed); `test_2fa` GREEN |
| 9 | Frontend surfaces the enriched row + kebab + two-section modal + guardrails (PROF-09) | ✓ VERIFIED | Sibling repo `accounts.tsx` (commits `55c5c64`, `1373bf6`, pushed to `origin/main`, deployed at `https://aimly.agsventurelab.com`, HTTP 200) contains avatar+`@username` row, `Изменить профиль`/`Обновить профиль` kebab, `Сохранить профиль`/`Обновить пароль 2FA` scoped CTAs, `recovery-email` two-step flow, client-side D-08 countdown guardrail (`cooldownRemainingMs`); `tsc --noEmit` exits 0 |

**Score:** 9/9 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `migrations/049_account_profile.sql` | 5 idempotent ADD COLUMN | ✓ VERIFIED | Present, applied to prod DB, idempotent `IF NOT EXISTS` |
| `app/models/__init__.py` (Sender) | ORM mirror w/ server_default | ✓ VERIFIED | Lines 130-138, `LargeBinary` imported |
| `app/schemas/__init__.py` | 7 new schemas + SenderResponse fields | ✓ VERIFIED | `ProfileUpdate`, `ProfileWarningItem`, `ProfileUpdateResponse`, `UsernameCheckResponse`, `TwoFAPasswordUpdate`, `RecoveryEmailStart`, `RecoveryEmailConfirm` all present; `EmailStr` imported |
| `app/services/telegram.py` | 15 profile/2FA/photo methods | ✓ VERIFIED | `update_profile`, `check_username`, `set_username`/`update_username`, `set_profile_photo`/`upload_profile_photo`, `delete_profile_photo`/`delete_profile_photos`, `resync_profile`/`fetch_profile`, `change_2fa_password`/`edit_2fa`, `start_recovery_email`/`set_recovery_email`, `confirm_recovery_email` — all present, per-op client pattern with `finally: disconnect_client`, all call sites carry the corrected `sender_id` positional arg (CR-04 fix) |
| `app/routers/senders.py` | 10 new endpoints + guardrail helpers | ✓ VERIFIED | `/username-check`, `PATCH /profile`, `POST/DELETE/GET /photo`, `POST /resync`, `POST /2fa`, `POST /2fa/recovery-email[/confirm]` all registered; `_check_profile_cooldown`/`_stamp_profile_change`/`_profile_advisory`/`_raise_profile_telegram_error` helpers present |
| `app/routers/onboarding.py` | tg_username cache at finalize | ✓ VERIFIED | Both create + re-auth-upsert paths write `tg_username` |
| `tests/test_account_profile.py` | 10 tests, all target behaviors | ✓ VERIFIED (WIRED + RUN) | 10/10 PASSED in isolation (ran directly, not just per SUMMARY claim) |
| `lovable-handoff/openapi.json` + `types/api.ts` | regenerated contract | ✓ VERIFIED | All 7 Phase-20 paths present; title `Outreach Platform API`; `tg_username` appears in generated types |
| `/root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/accounts.tsx` | enriched UI | ✓ VERIFIED | Present, pushed to `origin/main`, typecheck clean, deployed live |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|----|--------|---------|
| `PATCH /profile` | `update_profile`/`set_username` | `telegram_service.update_profile(...)`/`update_username(...)` | ✓ WIRED | Confirmed call sites lines 1115/1130 |
| `_check_profile_cooldown` | `Sender.profile_field_changed_at` | reads iso ts, raises 409 <1h | ✓ WIRED | Confirmed lines 251+ ; `test_cooldown_block` GREEN |
| `GET /photo` | `Sender.tg_photo` (BYTEA) | `Response(content=sender.tg_photo, media_type=...)` | ✓ WIRED | Confirmed line 1292 |
| `POST /photo` | `set_profile_photo`/`upload_profile_photo` | `telegram_service.upload_profile_photo(...)` | ✓ WIRED | Confirmed line 1196 |
| `POST /resync` | `resync_profile`/`fetch_profile` | `telegram_service.fetch_profile(...)` | ✓ WIRED | Confirmed line 1314; composes name (gap-fix) |
| `POST /2fa` | `change_2fa_password` via `client.edit_2fa` | `telegram_service.edit_2fa(...)` | ✓ WIRED | Confirmed line 1385; no `email=` kwarg (Pitfall-2 avoided) |
| `start_recovery_email` | `EmailUnconfirmedError` pivot | `GetPasswordRequest`+`compute_check`+`UpdatePasswordSettingsRequest` | ✓ WIRED | Confirmed in telegram.py; `test_2fa` GREEN |
| `accounts.tsx` avatar | `GET /api/v1/senders/{slug}/photo` | `fetchSenderPhoto` → object URL, initials fallback | ✓ WIRED | Confirmed line 729, 822 `AccountAvatar` |
| `accounts.tsx` Section B email step | `POST /2fa/recovery-email` then `/confirm` | two-step state machine | ✓ WIRED | Confirmed lines 1122/1134 |
| `get_client` callers (9 router sites, 15 service methods) | `get_client(sender_slug, sender_id, encrypted_session, ...)` | positional signature match | ✓ WIRED (post-fix) | CR-04 regression (Batch G/WR-14 signature drift) found + fixed in `b760d89`, locked by `tests/test_cr04_profile_call_signatures.py` (2/2 GREEN), deployed (api container rebuilt ~1h before verification) |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|---------------------|--------|
| `accounts.tsx` `SenderRow`/`AccountAvatar` | `sender.has_photo`, `sender.tg_username` | `GET /senders` (SenderResponse, `_sender_to_response`) → `bool(sender.tg_photo)` / `sender.tg_username` (real DB columns, no static fallback) | Yes | ✓ FLOWING |
| `accounts.tsx` `ProfileModal` guardrail countdown | `sender.profile_field_changed_at` | Same SenderResponse field, populated by `_stamp_profile_change` on real writes | Yes | ✓ FLOWING |
| `GET /senders/{slug}/photo` | `sender.tg_photo` bytes | Prod DB column, populated by upload/resync Telegram round-trip (verified BYTEA column has real data path, not a stub) | Yes | ✓ FLOWING |
| `AccountAvatar` photo `<img>` after **resync specifically** | `photoChangedAt` (`profile_field_changed_at.photo`) | resync intentionally does NOT stamp `photo` (read-only, no cooldown) → `useEffect` dependency never fires post-resync even though `tg_photo` bytes did update server-side | Partial (documented) | ⚠️ STATIC (known, accepted) — see Known Minor Gap below; name/bio/username refresh correctly, only the photo `<img>` element itself is stale until next full remount/query |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Migration 049 applied to running prod DB | `psql … SELECT version FROM schema_migrations WHERE version='049_account_profile'` | 1 row, applied 2026-07-04 09:24:11 | ✓ PASS |
| Prod `senders` table has the 5 columns | `psql … \d senders` | all 5 columns present with correct types/default | ✓ PASS |
| `tests/test_account_profile.py` full suite | `pytest tests/test_account_profile.py` (test-overlay) | 10 passed | ✓ PASS |
| Wave-merge regression (`test_account_profile` + `test_senders` + `test_onboarding`) | `pytest` (test-overlay) | 78 passed | ✓ PASS |
| CR-04 regression lock (get_client signature) | `pytest tests/test_cr04_profile_call_signatures.py` | 2 passed | ✓ PASS |
| Frontend typecheck | `npx tsc --noEmit` (sibling repo) | exit 0 | ✓ PASS |
| Frontend deployed live | `curl -o /dev/null -w '%{http_code}' https://aimly.agsventurelab.com/` | 200 | ✓ PASS |
| Backend openapi.json carries all 7 Phase-20 paths | `jq -e '.paths | has(...)'` | true | ✓ PASS |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| PROF-01 | 20-01 | Cached profile columns on senders (mig, ORM) | ✓ SATISFIED | Migration 049 applied prod; ORM mirror present |
| PROF-02 | 20-02 | Edit first/last name + bio, warning-only | ✓ SATISFIED | `PATCH /profile` + `test_update_name_bio` |
| PROF-03 | 20-02 | Edit username, pre-check, 1h hard block | ✓ SATISFIED | `GET /username-check` + `PATCH /profile` + `test_username`/`test_cooldown_block` |
| PROF-04 | 20-03 | Upload/delete profile photo, size/mime validated, 1h block | ✓ SATISFIED | `POST/DELETE /photo` + `test_photo` |
| PROF-05 | 20-04 | 2FA password set/change + recovery-email two-step, password never persisted | ✓ SATISFIED | `POST /2fa[...]` endpoints, D-03 grep-verified (no DB writes) + `test_2fa` |
| PROF-06 | 20-03 | Manual resync refreshes cache, read-only | ✓ SATISFIED | `POST /resync` + `test_resync`/`test_resync_updates_name` |
| PROF-07 | 20-03 | Auth-gated photo serve, never raw blob/base64 | ✓ SATISFIED | `GET /photo` returns `Response(content=...)`, list JSON carries only `has_photo: bool` |
| PROF-08 | 20-02 | Onboarding finalize caches tg_username (create + re-auth) | ✓ SATISFIED | `onboarding.py` both paths + `test_finalize_caches_profile` |
| PROF-09 | 20-05 | Frontend surface: row, kebab, modal, guardrails, handoff regen, human-UAT | ✓ SATISFIED | `accounts.tsx` (sibling repo, deployed) + regenerated `openapi.json`/types + human "approved" sign-off (20-05-SUMMARY) |

No orphaned requirements found — REQUIREMENTS.md §Phase 20 lists exactly PROF-01..09, all nine declared in plan frontmatter (20-01: PROF-01; 20-02: PROF-02/03/08; 20-03: PROF-04/06/07; 20-04: PROF-05; 20-05: PROF-09) and all nine cross-referenced above.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `app/routers/senders.py` / `onboarding.py` | multiple | `# TODO(v2-rls): replaced by RLS policy` | ℹ️ Info | Pre-existing project-wide marker unrelated to Phase 20 (present since prior phases); not a Phase-20 stub |
| `accounts.tsx` | 362, 1024, 1066, 1075 | `invalidateQueries({queryKey: ["sender-photo", slug]})` targets a key no `useQuery` reads | ⚠️ Warning | Documented known minor gap (avatar doesn't visually refresh after resync); explicitly accepted as non-blocking by the user during the human-verify gate; name/bio/username refresh correctly |

No blocker anti-patterns found. No hardcoded-empty stubs, no placeholder returns, no silently-swallowed error paths in the Phase-20 code (username-check's live-probe fallback deliberately falls through to `available=True` on unreachable session by design, documented and tested, with a `TypeError` re-raise guard added by CR-04 to prevent masking real bugs as "available").

### Human Verification Required

None outstanding. The phase's one manual-only item — the live 2FA recovery-email round-trip against a real Telegram inbox (RESEARCH §Sources tertiary, MEDIUM-LOW confidence) — was carried by Plan 20-05's blocking human-verify checkpoint and received explicit user approval ("approved") after two gap-closure rounds, per `20-05-SUMMARY.md`.

### Gaps Summary

No blocking gaps. Two items are worth carrying forward for completeness, both already explicitly triaged by the user during Phase 20 and excluded from verification failure per this task's instructions:

1. **Avatar photo staleness after resync** (known minor gap, documented in 20-05-SUMMARY "Known Gaps"): the `AccountAvatar` photo `<img>` doesn't visually refresh immediately after `POST /resync` because its `useEffect` keys on `profile_field_changed_at.photo`, a stamp resync intentionally never writes (resync is a read-only op by design, D-12). Server-side `tg_photo` bytes DO update correctly; only the already-rendered `<img>` element goes stale until next full data refetch/remount. User tested and explicitly accepted this as non-blocking.
2. **Bulk/mass account profile editing** — explicitly scoped OUT of Phase 20 by user agreement, captured as backlog item 999.1 (commit `09f9310`). Not a Phase-20 gap.

One noteworthy finding surfaced during verification (not a gap, already resolved before this verification ran): a cross-batch regression (CR-04, `b760d89`, quick-task 260706) had broken all 9 Phase-20 router call sites into `TelegramService` after an unrelated parallel task (Batch G/WR-14) added a required `sender_id` positional argument to `get_client`. This was live in prod for a period, silently degrading `username-check` to always report "available." It was caught by a re-review, fixed, locked with a dedicated regression test (`tests/test_cr04_profile_call_signatures.py`, 2/2 green), and deployed prior to this verification. Current running code and prod deployment reflect the fix; all Phase-20 tests are green against the corrected signatures.

---

*Verified: 2026-07-06T15:49:42Z*
*Verifier: Claude (gsd-verifier)*
