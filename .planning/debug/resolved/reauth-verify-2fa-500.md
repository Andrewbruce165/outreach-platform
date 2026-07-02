---
status: resolved
trigger: "пробовал сделать реавторизацию аккаунтов, но получил ошибку — проверь"
created: 2026-07-02
updated: 2026-07-02
specialist_hint: python
---

## Symptoms

DATA_START
- **Expected behavior:** Re-authorization of an existing Telegram sender account (session_expired) through the onboarding flow (phone → code → 2FA) completes and the existing sender row is updated with the new session_string / auth_status.
- **Actual behavior:** `POST /api/v1/onboarding/verify-2fa` returns **500 Internal Server Error**. The re-auth finalization attempts an INSERT into `senders` instead of updating the existing row.
- **Error message:** `sqlalchemy.exc.IntegrityError: (asyncpg.exceptions.UniqueViolationError) duplicate key value violates unique constraint "idx_senders_workspace_slug"` — raised from `app/routers/onboarding.py:585 verify_2fa` → `:280 _finalize_onboarding_or_reauth` → `:320 _create_sender_from_session` (INSERT INTO senders ...).
- **Timeline:** Two occurrences today 2026-07-02 ~11:08 and ~11:09 (both verify-2fa attempts, sessions 432fc33b and ef123cd6, both reached awaiting_2fa then 500). Context: 4 senders went `session_expired` (incl. checker accounts ca-account-1 / ca-account-2, see memory project-warmup-head-of-line-blocking); user attempted re-auth today via the UI onboarding flow.
- **Reproduction:** Re-authorize an already-registered sender account (same phone → same slug in same workspace) through /onboarding: phone → verify-code → verify-2fa. Finalization hits the unique index `idx_senders_workspace_slug` because a sender with that workspace_id+slug already exists.
DATA_END

## Current Focus

hypothesis: CONFIRMED. Re-auth done through the plain `/onboarding/start` flow (as the documented contract requires — reconciliation.md: no `/reauth` endpoint, re-auth "reuses the existing onboarding flow against the same slug") leaves `onboarding_sessions.original_sender_id = NULL`. `_finalize_onboarding_or_reauth` gates re-auth ONLY on `original_sender_id is not None`, so with NULL it falls through to `_create_sender_from_session`, which computes the deterministic `slug = sender-<telegram_id>` from `get_me().id` and blindly INSERTs → collides with the pre-existing `(workspace_id, slug)` row on `idx_senders_workspace_slug`.
test: Fix `_create_sender_from_session` to be idempotent: before INSERT, look up an existing sender by `(workspace_id, slug)` and UPDATE its session/auth_status if found (upsert semantics). Also populate `telegram_id` on the row (currently never set). Add regression test via test-overlay.
expecting: Re-auth through the plain flow updates the existing sender (auth_status→ok, new session_string) instead of raising UniqueViolationError. New (first-time) onboarding still INSERTs.
next_action: none — fix implemented, specialist-review hardening applied, tests green, committed. Deploy left to human (see Resolution).

## Evidence

- timestamp: 2026-07-02 11:08–11:10 — docker logs outreach-platform-api: two `POST /api/v1/onboarding/verify-2fa` → 500, traceback `verify_2fa` (onboarding.py:585) → `_finalize_onboarding_or_reauth` (:280) → `_create_sender_from_session` (:320) → INSERT INTO senders → UniqueViolationError on `idx_senders_workspace_slug`. verify-code succeeded (200) and onboarding sessions 432fc33b, ef123cd6 reached `awaiting_2fa` before the failure.
- timestamp: 2026-07-02 (investigation) — DB check: failing onboarding_sessions 432fc33b & ef123cd6 both have `phone=+79587869196`, `status=awaiting_2fa`, and **`original_sender_id = NULL`**. That phone already exists as sender `sender-8218483045` (id 6b0e6958…, telegram_id 8218483045, `auth_status=session_expired`) in the same workspace bb96789d. So the finalize code's re-auth branch (`if session_row.original_sender_id is not None`) never fires → falls through to create → deterministic slug `sender-8218483045` collides.
- timestamp: 2026-07-02 (investigation) — `idx_senders_workspace_slug` = UNIQUE btree on `(workspace_id, slug)`. Slug is deterministic: `_create_sender_from_session` computes `slug = f"sender-{get_me().id}"`. Same physical Telegram account → same telegram_id → same slug → guaranteed collision on any re-onboard of an existing account through the plain flow.
- timestamp: 2026-07-02 (investigation) — lovable-handoff/reconciliation.md L44-46 documents the DESIGN CONTRACT: "no `/reauth` endpoint — re-auth reuses the existing onboarding flow (phone → SMS code → success) against the same slug; backend writes the new encrypted session to the existing sender row via POST /onboarding/verify-code." I.e. the plain onboarding flow IS the intended re-auth path, and the backend is expected to update-in-place. The `original_sender_id` mechanism (later `/reauth/{slug}` endpoints) is a second, separate path that the UI is not required to use. The create path failing to upsert violates this documented contract.
- timestamp: 2026-07-02 (investigation) — secondary defect: `_create_sender_from_session` never sets `telegram_id` on the new Sender row (constructor omits it) despite computing slug from it. Existing rows have telegram_id only because they were onboarded by older code. Fix should populate telegram_id too.

## Eliminated

- hypothesis: lookup uses wrong key / wrong workspace_id inside `_load_existing_sender`.
  evidence: `_load_existing_sender` is never reached — the guard `if session_row.original_sender_id is not None` is False (original_sender_id NULL) so the re-auth branch is skipped entirely. The lookup logic itself is fine; the branch simply doesn't run for the plain-flow re-auth path.
  timestamp: 2026-07-02

## Specialist Review

Specialist: python (best-practices review of the applied fix, 2026-07-02).
Verdict: **SUGGEST_CHANGE** — fix direction correct; two hardening items, both **applied**:

1. **SELECT-then-INSERT race** (double-submitted verify-2fa, or verify-code + QR poll): both concurrent finalizations could miss the SELECT and both INSERT → same UniqueViolation 500 returns. Applied the recommended minimal recovery: the INSERT `commit()` is wrapped in `try/except IntegrityError` → `rollback()` → re-SELECT by `(workspace_id, slug)` → fall into the same update-in-place branch (`_update_in_place` helper). Preserves the deliberately narrow update-set vs a clunkier `ON CONFLICT DO UPDATE` with the encrypted-session ORM flow.
2. **Consistency gap in `_refresh_sender_session`** (explicit `/reauth/{slug}` fast path): it never set `telegram_id`, so legacy rows with NULL `telegram_id` stayed NULL forever. Applied a guarded backfill: when `sender.telegram_id is None`, `get_me()` (exception-tolerant) fills it.

Confirmed correct as-is (kept unchanged): field preservation — only `session_string`, `auth_status='ok'`, `telegram_id`, `proxy` (when provided) are touched; `restriction_status`, `restricted_until`, `lifecycle_status`, `checker_rest_until`, `checker_trip_count`, `rate_per_*`, `name`, `phone`, `role` preserved, so a parked/spam-limited checker does not silently reactivate on re-auth. `auth_status='ok'` IS the session-expired clear (session_expired is an auth_status value). `commit()` inside the helper matches existing convention (`_refresh_sender_session`).

## Decisions (non-interactive session)

- 2026-07-02 — Checkpoint "root cause found → fix options": auto-selected **Fix now** (goal=find_and_fix, non-interactive policy).
- 2026-07-02 — Specialist SUGGEST_CHANGE: auto-accepted both hardening items and applied them before commit.
- 2026-07-02 — Deploy intentionally NOT performed (per session instructions); left as recommendation below.

## Resolution

root_cause: Re-authorizing an existing sender through the plain onboarding flow (`/onboarding/start` → verify-code → verify-2fa) — which is the documented re-auth contract (reconciliation.md: no dedicated `/reauth` endpoint required, re-auth reuses onboarding against the same slug) — leaves `onboarding_sessions.original_sender_id = NULL`. `_finalize_onboarding_or_reauth` only routes to the UPDATE branch when `original_sender_id is not None`, so it falls through to `_create_sender_from_session`, which builds the deterministic slug `sender-<telegram_id>` and unconditionally INSERTs. Because the same physical Telegram account yields the same telegram_id → same slug, the INSERT violates the UNIQUE `(workspace_id, slug)` index (`idx_senders_workspace_slug`) → 500 UniqueViolationError.
fix: Made `_create_sender_from_session` (app/routers/onboarding.py) idempotent on `(workspace_id, slug)`: SELECT existing sender by that key → `_update_in_place` (session_string re-encrypted, auth_status='ok', telegram_id re-populated, proxy updated when supplied); otherwise INSERT (now also populating `telegram_id`, previously omitted). Specialist hardening applied on top: (a) `IntegrityError` recovery around the INSERT commit — on race, rollback + re-SELECT + update-in-place instead of 500; (b) `_refresh_sender_session` (explicit `/reauth` fast path) backfills NULL `telegram_id` via exception-tolerant `get_me()`. Explicit `/reauth/{slug}` routing logic unchanged.
verification: Regression test tests/test_onboarding_plainflow_reauth.py (3 tests) — reproduces the exact prod case (telegram_id=8218483045, original_sender_id NULL, pre-existing slug), asserts no IntegrityError + exactly 1 row. Via test-overlay after hardening: targeted file → 3 passed; full `-k "onboarding or reauth"` → 46 passed, 0 failed (first-time onboarding + explicit /reauth path all green). `py_compile` clean.
files_changed:
  - app/routers/onboarding.py — _create_sender_from_session upserts on (workspace_id, slug) + IntegrityError race recovery + populates telegram_id (both paths); _refresh_sender_session backfills NULL telegram_id
  - tests/test_onboarding_plainflow_reauth.py — new regression test (3 cases)
deploy_status: NOT deployed. Deploy requires `cd /root/apps/aimly/tg-outreach && docker compose up -d --build api` (restart alone does not pick up code changes). After deploy, live-verify: re-auth `sender-8218483045` (+79587869196) via the UI plain flow — verify-2fa should return 200 and update the existing row (auth_status→ok) instead of 500. NOTE: a parallel agent authored the initial (identical) fix + test in the working tree; independent investigation corroborated the root cause at code and data level, and this session added the specialist hardening on top.
