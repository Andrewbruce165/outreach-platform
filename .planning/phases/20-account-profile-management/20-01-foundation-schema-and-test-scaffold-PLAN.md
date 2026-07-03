---
phase: 20-account-profile-management
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - migrations/047_account_profile.sql
  - app/models/__init__.py
  - app/schemas/__init__.py
  - tests/test_account_profile.py
  - tests/test_onboarding.py
autonomous: true
requirements: [PROF-01]
must_haves:
  truths:
    - "The senders table has cached profile columns (tg_username, tg_bio, tg_photo, tg_photo_mime, profile_field_changed_at) after migration 047 applies"
    - "A raw-SQL INSERT into senders that omits profile_field_changed_at succeeds (server_default fires under create_all)"
    - "tests/test_account_profile.py collects clean (deferred in-body imports) and its PROF-01..08 + D-08/D-09 tests are RED"
  artifacts:
    - path: "migrations/047_account_profile.sql"
      provides: "idempotent ADD COLUMN IF NOT EXISTS for the 5 profile columns"
      contains: "ADD COLUMN IF NOT EXISTS profile_field_changed_at"
    - path: "app/models/__init__.py"
      provides: "Sender ORM mirror of the 5 new columns with server_default on the JSONB"
      contains: "profile_field_changed_at"
    - path: "app/schemas/__init__.py"
      provides: "ProfileUpdate / UsernameCheckResponse / TwoFAPasswordUpdate / RecoveryEmailStart / RecoveryEmailConfirm schemas + SenderResponse profile fields"
      contains: "class ProfileUpdate"
    - path: "tests/test_account_profile.py"
      provides: "Wave-0 RED scaffold covering PROF-01..08 + D-08/D-09"
      contains: "def test_profile_columns_defaults"
  key_links:
    - from: "app/models/__init__.py (Sender.profile_field_changed_at)"
      to: "migrations/047_account_profile.sql (DEFAULT '{}'::jsonb)"
      via: "server_default=text(\"'{}'::jsonb\") mirrors migration DEFAULT"
      pattern: "server_default=text"
---

<objective>
Lay the data + schema + test foundation for Account Profile Management. Add the cached-profile columns to `senders` via idempotent migration 047, mirror them on the `Sender` ORM (with `server_default` on the NOT NULL JSONB to dodge the create_all drift trap), add all Pydantic request/response schemas the later plans need, and land the Wave-0 RED test scaffold that pins PROF-01..08 + the D-08/D-09 guardrails.

Purpose: every downstream plan (identity, photo, 2FA, frontend) reads/writes these columns and binds to these schemas. Defining the contracts first prevents an executor scavenger hunt and gives the Nyquist sampler a target test file.
Output: migration 047, extended `Sender` ORM, new schemas, `tests/test_account_profile.py` (RED), extended `tests/test_onboarding.py` (RED PROF-08).
</objective>

<execution_context>
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/workflows/execute-plan.md
@/root/apps/aimly/tg-outreach/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md
@.planning/phases/20-account-profile-management/20-CONTEXT.md
@.planning/phases/20-account-profile-management/20-RESEARCH.md
@.planning/phases/20-account-profile-management/20-VALIDATION.md

<interfaces>
<!-- Current Sender ORM (app/models/__init__.py ~line 74). New columns go at the END of the column block, before the relationships. -->
Existing tail of Sender columns (mirror the server_default idiom):
```python
    checker_rest_until = Column(DateTime(timezone=True), nullable=True)
    checker_trip_count = Column(Integer, nullable=False, server_default='0')
    rate_per_min = Column(Integer, nullable=False, server_default='4')
    rate_per_hour = Column(Integer, nullable=False, server_default='20')
    rate_per_day = Column(Integer, nullable=False, server_default='150')
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_used_at = Column(DateTime(timezone=True), onupdate=func.now())
```
`text` is already imported in app/models/__init__.py (used by SenderRestrictionEvent.id server_default=text("gen_random_uuid()")). JSONB is already imported.

<!-- SenderResponse (app/schemas/__init__.py ~line 121). Add the profile fields near the bottom, before SenderCreateResponse. -->
Current SenderResponse already carries: id, slug, name, phone, status, checker_status, checker_trip_count, auth_status, lifecycle_status, restriction_status, restricted_until, rate_limits, role, proxy, last_used_at, created_at, sent_today, locked_by_campaign_id, locked_by_campaign_name. It uses `model_config = ConfigDict(from_attributes=True)`.

<!-- Migration precedent (migrations/046 / 035): idempotent ADD COLUMN IF NOT EXISTS, auto-applied by app/database.py::_apply_migrations, api fail-fasts if it raises. -->
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Migration 047 + Sender ORM columns</name>
  <read_first>
    - migrations/046_telegram_service_status.sql (idempotent pattern, BEGIN/COMMIT, DO $$ EXCEPTION)
    - migrations/035_checker_post_batch_rest.sql (single ADD COLUMN IF NOT EXISTS pattern + rationale comment style)
    - app/models/__init__.py (Sender class ~line 74-125; note `text` and `JSONB` already imported; SenderRestrictionEvent.id shows the server_default=text(...) idiom)
    - .planning/phases/20-account-profile-management/20-RESEARCH.md (§Migration + §Pitfall 1 ORM-drift)
  </read_first>
  <action>
Create `migrations/047_account_profile.sql` with a leading comment (mirror 035's rationale style: next free number is 047 after 046; auto-applied by _apply_migrations in lexical order; MUST be idempotent or api fail-fasts). Body — exactly these five idempotent statements:

```sql
ALTER TABLE senders ADD COLUMN IF NOT EXISTS tg_username        VARCHAR(32) NULL;
ALTER TABLE senders ADD COLUMN IF NOT EXISTS tg_bio             VARCHAR(140) NULL;
ALTER TABLE senders ADD COLUMN IF NOT EXISTS tg_photo           BYTEA NULL;
ALTER TABLE senders ADD COLUMN IF NOT EXISTS tg_photo_mime      VARCHAR(32) NULL;
ALTER TABLE senders ADD COLUMN IF NOT EXISTS profile_field_changed_at JSONB NOT NULL DEFAULT '{}'::jsonb;
```

No BEGIN/COMMIT wrapper is required for plain ADD COLUMN IF NOT EXISTS (matches 035). No backfill (NULL = "not yet cached"; `{}` = "no field ever changed").

Then add the mirror columns to the `Sender` ORM in `app/models/__init__.py`, immediately after `last_used_at` (line ~119), before the `# Relationships` comment:

```python
    # Phase 20 (PROF-01): cached Telegram profile (mig 047). NULL = not yet cached.
    tg_username = Column(String(32), nullable=True)
    tg_bio = Column(String(140), nullable=True)   # free ≤70 / premium ≤140; AboutTooLongError is the runtime backstop
    tg_photo = Column(LargeBinary, nullable=True)  # small square avatar bytes, served via authenticated endpoint (D-11)
    tg_photo_mime = Column(String(32), nullable=True)
    # Per-field cooldown STATE (not a log): {"username": iso8601, "photo": iso8601, "name": ..., "bio": ...}.
    # server_default MANDATORY (memory project-orm-default-vs-server-default-drift): create_all builds the
    # test/fresh-DB schema from the ORM, not the migration — a NOT NULL column without server_default breaks
    # raw INSERTs (_insert_sender_raw) that omit it.
    profile_field_changed_at = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
```

Confirm `LargeBinary` is imported in app/models/__init__.py (KbDocument.raw_content uses it — grep). If not imported at the top-level sqlalchemy import, add it to the existing `from sqlalchemy import (...)` line.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_account_profile.py::test_profile_columns_defaults -x</automated>
  </verify>
  <acceptance_criteria>
    - `migrations/047_account_profile.sql` exists and contains `ADD COLUMN IF NOT EXISTS profile_field_changed_at JSONB NOT NULL DEFAULT '{}'::jsonb`
    - `migrations/047_account_profile.sql` contains all 5 columns: `tg_username`, `tg_bio`, `tg_photo`, `tg_photo_mime`, `profile_field_changed_at`
    - `app/models/__init__.py` grep `profile_field_changed_at = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))` returns a match
    - `app/models/__init__.py` grep `tg_photo = Column(LargeBinary` returns a match
    - `grep -n "LargeBinary" app/models/__init__.py` shows it imported
    - test_profile_columns_defaults passes (raw INSERT omitting profile_field_changed_at succeeds; the 5 columns exist)
  </acceptance_criteria>
  <done>Migration 047 present and idempotent; Sender ORM mirrors the 5 columns with server_default on the JSONB; the columns-defaults test is GREEN.</done>
</task>

<task type="auto">
  <name>Task 2: Pydantic schemas + SenderResponse profile fields</name>
  <read_first>
    - app/schemas/__init__.py (SenderUpdate ~line 107, SenderResponse ~line 121, SenderCreateResponse ~line 168 — note ConfigDict/Field/Literal/Optional import style)
    - .planning/phases/20-account-profile-management/20-UI-SPEC.md (§Surface 3 field list + §Copywriting for field labels)
    - .planning/phases/20-account-profile-management/20-RESEARCH.md (§Architecture Patterns structure block + §Open Question 2 bio 70 vs 140)
  </read_first>
  <action>
In `app/schemas/__init__.py`, add profile fields to `SenderResponse` (after `locked_by_campaign_name`, before the class ends) so the account list + edit form can render cache-only:

```python
    # Phase 20 (PROF-01/07/D-08): cached profile surfaced for the enriched row + edit form.
    tg_username: Optional[str] = None
    tg_bio: Optional[str] = None
    has_photo: bool = False   # list carries only this bool; photo bytes served via GET /senders/{slug}/photo (D-11)
    # Per-field last-change timestamps (iso8601 strings) so the UI can compute the D-08 1h countdown client-side.
    profile_field_changed_at: dict = {}
```

Then add these NEW schema classes (place them right after `SenderCreateResponse`, ~line 172). Use `EmailStr` (email-validator==2.1.0 is installed — verified in requirements.txt):

```python
class ProfileUpdate(BaseModel):
    """PATCH /senders/{slug}/profile — Section A identity. Only non-None fields are written.
    username="" clears the username; username=None leaves it untouched (D-07/D-08)."""
    first_name: Optional[str] = Field(None, max_length=64)
    last_name: Optional[str] = Field(None, max_length=64)
    about: Optional[str] = Field(None, max_length=70)          # bio; AboutTooLongError is the premium backstop
    username: Optional[str] = Field(None, max_length=32)

class UsernameCheckResponse(BaseModel):
    """GET /senders/{slug}/username-check?username= (C5)."""
    available: bool
    reason: Optional[str] = None   # 'taken' | 'invalid' | None

class TwoFAPasswordUpdate(BaseModel):
    """POST /senders/{slug}/2fa — password set/change (D-03/D-04). Password never persisted."""
    current_password: Optional[str] = None   # required only if 2FA already set (D-04)
    new_password: str = Field(..., min_length=1)
    hint: Optional[str] = Field(None, max_length=100)

class RecoveryEmailStart(BaseModel):
    """POST /senders/{slug}/2fa/recovery-email — step 1 (D-02/D-04)."""
    current_password: Optional[str] = None
    email: EmailStr

class RecoveryEmailConfirm(BaseModel):
    """POST /senders/{slug}/2fa/recovery-email/confirm — step 2 (code only, no SRP)."""
    code: str = Field(..., min_length=1)
```

Confirm `EmailStr` is imported from pydantic at the top of the file; if not, add it to the existing `from pydantic import (...)` line. Confirm `Optional`, `Field`, `BaseModel` already imported (they are — used throughout).
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_account_profile.py -x --collect-only</automated>
  </verify>
  <acceptance_criteria>
    - `app/schemas/__init__.py` grep `class ProfileUpdate` returns a match
    - `app/schemas/__init__.py` grep `class TwoFAPasswordUpdate` and `class RecoveryEmailStart` and `class RecoveryEmailConfirm` and `class UsernameCheckResponse` each return a match
    - `app/schemas/__init__.py` grep `has_photo: bool = False` returns a match inside SenderResponse
    - `grep -n "EmailStr" app/schemas/__init__.py` shows it imported
    - `pytest tests/test_account_profile.py --collect-only` exits 0 (module imports cleanly; no collection error)
  </acceptance_criteria>
  <done>SenderResponse exposes tg_username/tg_bio/has_photo/profile_field_changed_at; the 5 new request/response schemas exist and import cleanly.</done>
</task>

<task type="auto">
  <name>Task 3: Wave-0 RED test scaffold (test_account_profile.py + onboarding PROF-08)</name>
  <read_first>
    - tests/test_senders.py (reuse `_create_workspace_via_jwt` line ~21 and `_insert_sender_raw` line ~32 helpers — import them or copy the pattern)
    - tests/test_onboarding.py (`_make_mock_client` line ~61, `_patch_factory` line ~101, `_bootstrap_workspace` line ~110 — the Telethon mocking model)
    - .planning/phases/20-account-profile-management/20-VALIDATION.md (§Per-Task Verification Map + §Wave 0 Requirements — the exact test names)
    - .planning/phases/20-account-profile-management/20-RESEARCH.md (§Validation Architecture → Phase Requirements → Test Map)
  </read_first>
  <action>
Create `tests/test_account_profile.py` following the Phase 13/16/17/18 RED-scaffold convention: **deferred in-body imports** of anything from `app.routers.senders` / `app.services.telegram` profile methods so `--collect-only` stays clean while the behavioural asserts are RED until later plans land.

Write these test functions (names are contractually referenced by 20-VALIDATION.md and downstream plans — use them verbatim):

- `test_profile_columns_defaults` — GREEN target for THIS plan: bootstrap a workspace, `_insert_sender_raw` a sender OMITTING profile_field_changed_at, then `SELECT tg_username, tg_bio, tg_photo, tg_photo_mime, profile_field_changed_at FROM senders WHERE id=...` — assert row exists, profile_field_changed_at == {} (server_default fired), the four nullable cols are NULL. (This must pass now.)
- `test_update_name_bio` — RED: PATCH /senders/{slug}/profile with {first_name, about}; assert 200 and that the mocked TelegramService.update_profile was dispatched an `UpdateProfileRequest`; oversized about → 400 BIO_TOO_LONG.
- `test_username` — RED: username pre-check + set; taken → 400 USERNAME_TAKEN; submitting current username → success no-op; 1h-ago change → 409.
- `test_photo` — RED: POST /senders/{slug}/photo multipart dispatches upload_file + UploadProfilePhotoRequest; DELETE clears; >5MB → 413 FILE_TOO_LARGE; non-jpg/png → 422 UNSUPPORTED_FILE_TYPE; 1h-ago change → 409.
- `test_2fa` — RED: POST /2fa set/change dispatches edit_2fa; wrong current → 400 PASSWORD_INVALID; POST /2fa/recovery-email → 200 EMAIL_CONFIRMATION_SENT + code_length; confirm endpoint dispatches ConfirmPasswordEmailRequest.
- `test_resync` — RED: POST /senders/{slug}/resync refreshes tg_username/tg_bio/has_photo from mocked get_me/GetFullUser/download_profile_photo.
- `test_photo_serve_auth` — RED: GET /senders/{slug}/photo returns bytes + correct media_type when a photo is cached; requires JWT (401/403 without); foreign-workspace slug → 404.
- `test_cooldown_block` — RED (D-08): seed profile_field_changed_at={'username': <now-30min iso>}; PATCH username → 409 TOO_FREQUENT with retry seconds; name/bio never 409.
- `test_warmup_advisory_not_blocking` — RED (D-09): sender lifecycle_status='warmup' OR created_at < 7 days → profile edit succeeds (200), advisory surfaced (warnings[] present) but NOT blocked.

Use `AsyncMock` for the mocked Telethon dispatch; assert on the TL request type via `.call_args` (Phase 17 request-type-introspection style). API-level tests bootstrap via `_create_workspace_via_jwt` and insert senders via `_insert_sender_raw`.

Then extend `tests/test_onboarding.py` with `test_finalize_caches_profile` (RED, PROF-08): drive the onboarding finalize with a `_make_mock_client` whose `get_me()` returns a `username`, and assert the created sender row has `tg_username` populated after finalize.

All new behavioural tests MUST currently be RED (the endpoints/methods do not exist yet). `test_profile_columns_defaults` must be GREEN.
  </action>
  <verify>
    <automated>docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_account_profile.py --collect-only && docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_account_profile.py::test_profile_columns_defaults -x</automated>
  </verify>
  <acceptance_criteria>
    - `tests/test_account_profile.py` exists and grep shows all 10 test function names: `test_profile_columns_defaults`, `test_update_name_bio`, `test_username`, `test_photo`, `test_2fa`, `test_resync`, `test_photo_serve_auth`, `test_cooldown_block`, `test_warmup_advisory_not_blocking`
    - `pytest tests/test_account_profile.py --collect-only` exits 0 (no import error at module scope — imports of not-yet-existing symbols are inside test bodies)
    - `test_profile_columns_defaults` PASSES (GREEN)
    - `tests/test_onboarding.py` grep `def test_finalize_caches_profile` returns a match
    - The other 8 behavioural tests are collected and RED (fail on missing endpoint/method, not on collection)
  </acceptance_criteria>
  <done>Wave-0 scaffold lands: collect-only is clean, PROF-01 columns test is GREEN, PROF-02..08 + D-08/D-09 tests are RED and named per 20-VALIDATION.md.</done>
</task>

</tasks>

<verification>
- Migration 047 applies idempotently (api starts; re-run is a no-op).
- `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_account_profile.py --collect-only` exits 0.
- `test_profile_columns_defaults` GREEN; the 8 behavioural tests + PROF-08 RED.
- Full suite still collects with 0 errors (baseline ~896+): `... run --rm api pytest --collect-only`.
</verification>

<success_criteria>
- 5 new senders columns present in prod schema + ORM (server_default on the JSONB).
- All Phase-20 request/response schemas defined and importable.
- RED scaffold in place naming every PROF/D test the downstream plans will turn GREEN.
</success_criteria>

<output>
After completion, create `.planning/phases/20-account-profile-management/20-01-SUMMARY.md`.
</output>
