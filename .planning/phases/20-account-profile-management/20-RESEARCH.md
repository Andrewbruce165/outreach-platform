# Phase 20: Account Profile Management - Research

**Researched:** 2026-07-03
**Domain:** Telethon 1.42 account-profile TL functions + FastAPI multipart/binary serving + Postgres BYTEA + per-field cooldown state
**Confidence:** HIGH (all Telethon signatures verified live in the running `api` container; all code patterns read from the actual brownfield source)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Editable field scope**
- **D-01:** Full scope per user request: first/last name, username, bio (about), profile photo (upload + delete), 2FA password (set/change), recovery email. Supersedes the seed's exclusion of 2FA/email.
- **D-02:** Telegram has no standalone "account email" concept — the only email is the 2FA **recovery email**, set/changed together with the 2FA password via `account.updatePasswordSettings` (Telethon: `client.edit_2fa(current_password=..., new_password=..., new_recovery_email=...)`).

**2FA change flow**
- **D-03:** User types the current 2FA password manually in the form every time a change is submitted (mirrors native Telegram UX) — the platform NEVER stores the 2FA password anywhere (not even encrypted). Only the Telegram session string is encrypted/stored, as today.
- **D-04:** If the account has no 2FA yet, flow is "set new" (no current-password field). If already set, current password is required before accepting a new password and/or recovery email.
- **D-05:** Lost/forgotten current password → out of scope for in-app recovery; user goes through Telegram's own recovery-email flow outside the platform.

**Anti-spam guardrail (frequency limiting)**
- **D-06:** Reuse the seed's warning-modal pattern before saving profile changes, with per-field frequency tracking (`last_profile_change_at`-style, per field).
- **D-07:** Name and bio changes → warning only, never blocked (Telegram doesn't hard-limit these; advisory).
- **D-08:** Username and photo changes → **hard block** (not just warning) if the same field was changed less than 1 hour ago. Save button stays disabled with a countdown/message.
- **D-09:** Accounts in `lifecycle_status='warmup'` or younger than 7 days (from `senders.created_at`) → the warning modal additionally mentions warmup/age, but editing is **NOT blocked** — advisory only, consistent with D-07.

**Data source & caching strategy**
- **D-10:** Cache profile fields (username, bio, photo) on the `Sender` row. Populate at onboarding finalize and refresh after every successful profile edit. Account-list reads are cache-only (fast, no per-render Telegram round-trip).
- **D-11:** Photo storage: BYTEA column in Postgres (small square avatar, compressed, expected ≤~200KB) — no object storage/CDN exists. Served through a platform API endpoint (same principle as encrypted session handling — data stays server-side, NEVER exposed as a raw blob URL).
- **D-12:** The "Update" action on an account card is repurposed as **manual resync**: re-fetch the live Telegram profile (`GetFullUser`) and refresh cached username/bio/photo — for profiles changed manually via the native client outside the platform. Does NOT open the edit form and does NOT relate to the old "refresh status" affordance (redundant — status derives fresh on every page load).

**Account list card updates**
- **D-13:** Cards display: photo (cached avatar or fallback initials), name, username, phone, plus existing actions (delete, reauth) plus the new "Update"/resync action. Reauth and delete already exist — this phase only adds new profile fields to the card + the resync action.
- **D-14:** Clicking a card / an explicit "Edit" action opens the full profile edit form (name/username/bio/photo/2FA/email) — distinct from the card-level "Update" resync button.

### Claude's Discretion
- Exact DB schema for per-field frequency tracking (JSONB `{field: timestamp}` column on `Sender` vs. a small dedicated history table).
- Whether to persist a change-history log for audit — not requested; only minimal frequency-check state required.
- Exact Telethon call sequence and error handling for the profile-edit functions.
- Exact wording/thresholds in the warning modal (seed tone: "не чаще 1 раза в день для username", "не чаще раза в неделю для фото"; this phase's hard limit is narrower: 1h block per D-08).
- Migration file numbering (next available: `047_...`).
- Photo upload endpoint request shape (multipart vs base64) and max upload size validation.
- Whether recovery-email is exposed as read-only (masked) if already set, vs. always requiring full re-enter.

### Deferred Ideas (OUT OF SCOPE)
- Privacy settings (`account.setPrivacy` — who sees phone/photo/last_seen).
- Change-history/audit log of profile edits.
- Bulk "apply this avatar to all my accounts" operation.
- Blocking profile edits during warmup entirely (considered and rejected — D-09).
</user_constraints>

<phase_requirements>
## Phase Requirements

No formally-numbered requirements exist yet in REQUIREMENTS.md for this phase (the traceability table stops at NORP-13). This phase promotes seed `.planning/seeds/account-profile-self-serve.md` **PROF-01** and expands it (2FA + recovery email, per D-01). The planner should **derive `PROF-01..PROF-NN`** during `/gsd:plan-phase 20` from the CONTEXT.md decisions and append them to REQUIREMENTS.md (same pattern as WARM-/SRLD-/LLMP-/NORP- phases). Suggested decomposition mapped to research findings below:

| Proposed ID | Behavior (from CONTEXT decision) | Research support |
|-------------|----------------------------------|------------------|
| PROF-01 | New cached profile columns on `senders` (username, bio, photo BYTEA, per-field change timestamps); idempotent migration 047; ORM mirror with server_default | §Architecture Pattern 1, §Migration, §Pitfall 1 |
| PROF-02 | Edit name/last-name/bio via `account.UpdateProfileRequest` (warning-only guardrail, D-07) | §Standard Stack, §Code Example 1 |
| PROF-03 | Edit username via `account.UpdateUsernameRequest` + `CheckUsernameRequest` pre-check; 1h hard block (D-08) | §Code Example 2, §Pitfall 4 |
| PROF-04 | Upload/delete profile photo (`upload_file` → `UploadProfilePhotoRequest`; `GetUserPhotos`→`DeletePhotosRequest`); 1h hard block (D-08) | §Code Example 3, §Code Example 4 |
| PROF-05 | 2FA password set/change (`edit_2fa`, no email) + recovery-email flow (raw two-request confirm) (D-03/D-04) | §Code Example 5, §Pitfall 2 (CRITICAL), §Open Question 1 |
| PROF-06 | Manual resync (`GetFullUser` + `download_profile_photo`) refreshes cache (D-12) | §Code Example 6 |
| PROF-07 | Serve cached photo via authenticated platform endpoint (no raw blob URL, D-11) | §Architecture Pattern 3, §Pitfall 3 |
| PROF-08 | Populate cache at onboarding finalize (D-10) | §Integration Points |
| PROF-09 | Frontend: richer account cards + edit form + resync action; regen openapi.json cross-repo (D-13/D-14) | §Integration Points |
</phase_requirements>

## Summary

Everything this phase needs is available and verified in the deployed stack: **Telethon 1.42.0** exposes every required TL function (`account.UpdateProfileRequest`, `account.UpdateUsernameRequest`, `account.CheckUsernameRequest`, `photos.UploadProfilePhotoRequest`, `photos.DeletePhotosRequest`, `photos.GetUserPhotosRequest`, `users.GetFullUserRequest`, the high-level `client.edit_2fa(...)` / `client.upload_file(...)` / `client.download_profile_photo(..., file=bytes)`), and the codebase already has all the plumbing patterns needed (per-operation `TelegramService` client lifecycle, `LargeBinary` BYTEA columns via `KbDocument.raw_content`, multipart `UploadFile` upload in `knowledge_bases.py`, idempotent `ADD COLUMN IF NOT EXISTS` migrations, workspace-scoped router endpoints under `auth_dep`).

There is **one hard architectural constraint that must drive the plan**: `client.edit_2fa(email=...)` requires a **synchronous `email_code_callback`** that is invoked *during* the call to return the code Telegram e-mails to the new recovery address. Because this project's `TelegramService` creates a client per-operation and disconnects it immediately (a hard rule — persistent connections steal listener updates), a single stateless HTTP request cannot block waiting for the user to read their email. Therefore **password-only** changes work cleanly through `edit_2fa` in one request, but **recovery-email changes require a two-request confirmation flow driven by the raw functions** (`account.GetPasswordRequest` → `telethon.password.compute_check` → `account.UpdatePasswordSettingsRequest` which raises `EmailUnconfirmedError` → later `account.ConfirmPasswordEmailRequest(code)`). This is the single biggest planning decision and is detailed in Pitfall 2 and Open Question 1.

**Primary recommendation:** Add the cached-profile columns to `senders` (username, bio, `photo BYTEA`, and a single JSONB `profile_field_changed_at {field: iso_ts}` for the D-08 cooldown check) via migration 047; add self-contained profile methods to `TelegramService` following the `send_message_by_telegram_id` shape (create client → op → `disconnect_client` in `finally`); add workspace-scoped endpoints to `app/routers/senders.py`; serve the cached photo bytes through an authenticated `Response(content=..., media_type=...)` endpoint; and split the 2FA flow into a clean `edit_2fa` password path plus a raw two-request recovery-email path.

## Standard Stack

### Core (all already installed — verified `pip show` in the `api` container)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Telethon | 1.42.0 | All profile TL functions + high-level `edit_2fa`/`upload_file`/`download_profile_photo` | Already the project's Telegram client; per-operation pattern established |
| FastAPI | 0.109.0 | `UploadFile`/`File(...)` multipart + `Response`/`StreamingResponse` binary serving | Already used for KB multipart upload |
| SQLAlchemy | 2.0.25 (async) | `LargeBinary` (BYTEA) + `JSONB` columns; async everywhere | Established ORM; `KbDocument.raw_content` is the BYTEA precedent |
| python-multipart | 0.0.6 | Backs FastAPI `UploadFile` | Already installed for KB upload |

**No new dependencies required.** Verified: `docker compose exec -T api pip show telethon` → `Version: 1.42.0`.

### Telethon TL functions/types used (all confirmed present in 1.42.0)
| Symbol | Import | Signature (verified live) |
|--------|--------|---------------------------|
| `UpdateProfileRequest` | `telethon.tl.functions.account` | `(first_name=None, last_name=None, about=None)` — send only fields you want to change |
| `UpdateUsernameRequest` | `telethon.tl.functions.account` | `(username: str)` — pass `""` to clear |
| `CheckUsernameRequest` | `telethon.tl.functions.account` | `(username: str)` → returns `bool` (True = available) |
| `UploadProfilePhotoRequest` | `telethon.tl.functions.photos` | `(file=<InputFile>, ...)` — `file` from `client.upload_file(...)` |
| `DeletePhotosRequest` | `telethon.tl.functions.photos` | `(id: List[InputPhoto])` |
| `GetUserPhotosRequest` | `telethon.tl.functions.photos` | `(user_id, offset:int, max_id:int, limit:int)` |
| `GetFullUserRequest` | `telethon.tl.functions.users` | `(id)` → `.full_user.about`, `.full_user.profile_photo` |
| `GetPasswordRequest` / `UpdatePasswordSettingsRequest` / `ConfirmPasswordEmailRequest` | `telethon.tl.functions.account` | raw SRP 2FA path (see Pitfall 2) |
| `compute_check` | `telethon.password` | `(request: account.Password, password: str)` → SRP `InputCheckPasswordSRP` |
| `PasswordInputSettings` | `telethon.tl.types.account` | `(new_algo=None, new_password_hash=None, hint=None, email=None, ...)` |
| `client.edit_2fa` | high-level | `(current_password=None, new_password=None, *, hint='', email=None, email_code_callback=None) -> bool` |
| `client.upload_file` | high-level | `(file, *, file_name=None, ...) -> InputFile` |
| `client.download_profile_photo` | high-level | `(entity, file=None, *, download_big=True)` — `file=bytes` returns a bytestring in-memory |
| `client.get_profile_photos` | high-level | `(entity, limit=None, *, offset=0, max_id=0)` → iterable of `Photo` (convenience over `GetUserPhotosRequest`) |

### Telethon error types (all confirmed present in `telethon.errors` 1.42.0)
| Error | Raised when | Map to |
|-------|-------------|--------|
| `UsernameOccupiedError` | username already taken | 400 `USERNAME_TAKEN` |
| `UsernameInvalidError` | bad format (not a-z0-9_, wrong length) | 400 `USERNAME_INVALID` |
| `UsernameNotModifiedError` | new username == current | treat as success/no-op |
| `UsernamePurchaseAvailableError` | username is a paid/fragment handle | 400 `USERNAME_PURCHASE_REQUIRED` |
| `AboutTooLongError` | bio > 70 chars | 400 `BIO_TOO_LONG` (also enforce in Pydantic) |
| `FirstNameInvalidError` | invalid first name | 400 `NAME_INVALID` |
| `PhotoCropSizeSmallError` | photo too small | 400 `PHOTO_TOO_SMALL` |
| `PhotoExtInvalidError` | unsupported image format | 400 `PHOTO_FORMAT_INVALID` |
| `PasswordHashInvalidError` | wrong current 2FA password | 400 `PASSWORD_INVALID` (reuse onboarding's existing code) |
| `EmailUnconfirmedError` | recovery email set but needs a code (carries `code_length`) | 200 `EMAIL_CONFIRMATION_SENT` (drives step 2) |
| `EmailInvalidError` | malformed email | 400 `EMAIL_INVALID` |
| `PasswordTooFreshError` / `SessionTooFreshError` | password/session set too recently — Telegram enforces a waiting period | 409 `TOO_FRESH` (carries seconds) — see Pitfall 5 |
| `FloodWaitError` | rate limited (carries `.seconds`) | 429 `FLOOD_WAIT` (reuse onboarding's `_map_telethon_error`) |

**Alternatives considered:** none needed — Telethon is the only Telegram client in the stack and covers 100% of the scope.

**Version verification:** `telethon==1.42.0` pinned in `requirements.txt`; confirmed running in the `api` container (`pip show`). PyPI current stable line is 1.4x; no upgrade required or recommended for this phase (the whole app is built on 1.42 idioms).

## Architecture Patterns

### Recommended structure (extend existing files — do NOT create new modules)
```
app/
├── models/__init__.py          # + new Sender columns (username, bio, photo_blob, photo_mime, profile_field_changed_at JSONB)
├── schemas/__init__.py         # + ProfileUpdate / ProfileResponse / TwoFAUpdate / RecoveryEmailConfirm / UsernameCheck schemas
├── services/telegram.py        # + TelegramService profile methods (self-contained client-per-op)
├── routers/senders.py          # + profile-edit endpoints + photo-serve + resync (workspace-scoped, auth_dep)
└── migrations/047_account_profile.sql   # idempotent ADD COLUMN IF NOT EXISTS
```

### Pattern 1: Self-contained per-operation TelegramService method
All new profile calls MUST follow the `send_message_by_telegram_id` shape (verified `app/services/telegram.py:1053`): the method **creates its own client** via `get_client(...)`, does the op, and **always disconnects in `finally`**. Do NOT hold a persistent client — a hard rule (`TelegramService` docstring line 256-259: persistent connections steal updates from the listener container).

```python
# Source: app/services/telegram.py:1053-1111 (send_message_by_telegram_id) — mirror this exactly
async def update_profile(self, sender_slug, encrypted_session, *, first_name=None,
                         last_name=None, about=None, proxy=None) -> dict:
    client = None
    try:
        client = await self.get_client(sender_slug, encrypted_session, proxy=proxy)
        from telethon.tl.functions.account import UpdateProfileRequest
        await client(UpdateProfileRequest(first_name=first_name, last_name=last_name, about=about))
        return {"success": True}
    except FloodWaitError as e:
        return {"success": False, "error": {"code": "FLOOD_WAIT", "retry_after": e.seconds}}
    # ... map Username*/About*/Password* errors to structured dicts (mirror send_message)
    finally:
        if client:
            await self.disconnect_client(client)
```

### Pattern 2: Workspace-scoped endpoint on the existing senders router
Every new endpoint follows `app/routers/senders.py` conventions exactly: `Depends(auth_dep)` for `AuthCtx`, `_load_sender_by_slug(db, ctx, slug)` for the workspace-scoped lookup (opaque 404 — never leak cross-tenant existence), structured `detail={"code": ..., "message": ...}` errors, and the `SessionAuthError → 403 AUTH_ERROR` mapping already used by `spambot-check` (lines 728-736). Reuse the exact `try/except SessionAuthError / finally disconnect_client` shape from the `spambot-check` handler (lines 680-745).

### Pattern 3: Serve BYTEA photo through an authenticated endpoint (never a raw URL, D-11)
No `StreamingResponse`/media-serving endpoint exists yet in `app/routers/` (the only binary today is the QR data-URI in onboarding, `_make_qr_image`, base64). For the avatar, add a workspace-scoped `GET /senders/{slug}/photo` that reads the cached BYTEA and returns `Response(content=blob, media_type=sender.photo_mime or "image/jpeg")`. This keeps the bytes server-side and gated by `auth_dep` (D-11 "same pattern as encrypted session — never a raw blob URL"). For the account-list JSON, expose only a boolean `has_photo` + the photo endpoint path (or omit the blob entirely) — do NOT inline base64 blobs into the list response (bloats every list render; the list is cache-only and hit often).

### Anti-Patterns to Avoid
- **Persistent Telegram client for profile ops** — steals listener updates. Always per-operation + `finally: disconnect_client`.
- **Inlining photo bytes/base64 in `SenderResponse`** — every account-list render would carry N avatars. Serve via a dedicated endpoint; put `has_photo: bool` in the list.
- **Storing the 2FA password** (even encrypted) — D-03 forbids it. It is a transient request field only.
- **`edit_2fa(email=...)` in a single request** — the callback blocks (see Pitfall 2).
- **Blind `UpdateProfileRequest` with all three fields always** — only pass the fields the user actually changed (None leaves a field untouched); sending unchanged fields is unnecessary write-churn Telegram counts against frequency limits.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| 2FA SRP password hashing | Manual SRP / PBKDF2 handshake | `client.edit_2fa(...)` (password-only) or `telethon.password.compute_check(pwd_obj, password)` (email path) | SRP is cryptographically fiendish; Telethon ships a correct, maintained impl |
| Username availability check | Guess-and-catch `UpdateUsername` | `account.CheckUsernameRequest(username)` → bool | Purpose-built pre-check; avoids burning an update attempt / frequency counter |
| Profile photo download to bytes | Manual `GetUserPhotos` + `upload.GetFile` chunk loop + file-reference handling | `client.download_profile_photo(entity, file=bytes)` | Handles DC routing, file references, chunking; returns a bytestring directly |
| Photo InputFile for upload | Manual `SaveFilePart` chunking | `await client.upload_file(bytes_or_path)` → pass as `file=` to `UploadProfilePhotoRequest` | Handles part sizing, big-file routing |
| Telethon error → HTTP mapping | New mapper | Extend `onboarding.py::_map_telethon_error` (already handles `PasswordHashInvalidError`/`FloodWaitError`) | One canonical mapper; consistent codes |
| Idempotent migration application | Manual apply logic | Drop `047_*.sql` in `migrations/` (`app/database.py::_apply_migrations` auto-applies) | Established; fail-fast + advisory-lock already built |

**Key insight:** The Telegram-side complexity (SRP, file references, DC routing, chunking) is entirely covered by Telethon high-level helpers. The *real* work in this phase is (a) the stateless-web adaptation of the email-confirmation flow, (b) the caching/serving of the photo blob, and (c) the per-field cooldown guardrail — none of which Telethon does for you.

## Migration (idempotent, file `047_account_profile.sql`)

Follow the exact pattern of `035_checker_post_batch_rest.sql` and `046_telegram_service_status.sql` (read both). All statements `ADD COLUMN IF NOT EXISTS`; no backfill needed (NULL = "not yet cached / never changed"). Auto-applied at api start by `_apply_migrations`; api fail-fasts if it raises, so it MUST be idempotent.

```sql
-- 047: cached Telegram profile fields on senders + per-field change cooldown state.
ALTER TABLE senders ADD COLUMN IF NOT EXISTS tg_username        VARCHAR(32) NULL;
ALTER TABLE senders ADD COLUMN IF NOT EXISTS tg_bio             VARCHAR(140) NULL;   -- Telegram premium bio ≤140; free ≤70
ALTER TABLE senders ADD COLUMN IF NOT EXISTS tg_photo           BYTEA NULL;
ALTER TABLE senders ADD COLUMN IF NOT EXISTS tg_photo_mime      VARCHAR(32) NULL;
ALTER TABLE senders ADD COLUMN IF NOT EXISTS profile_field_changed_at JSONB NOT NULL DEFAULT '{}'::jsonb;
```

**ORM-drift rule (mandatory — memory `project-orm-default-vs-server-default-drift.md`):** every NOT NULL column added here MUST have a matching `server_default` on the `Sender` ORM column, because `create_all` (test/fresh-DB path) builds tables from the ORM, not the migration — a `NOT NULL` column without `server_default` breaks raw INSERTs that omit it. So `profile_field_changed_at` → `Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))`. The nullable BYTEA/VARCHAR columns need no server_default.

## Frequency-tracking schema recommendation (Claude's Discretion)

**Recommendation: single JSONB column `profile_field_changed_at` on `senders`, shape `{"username": "<iso8601>", "photo": "<iso8601>", "name": "...", "bio": "..."}`.** HIGH confidence this is the right call for this phase:

- The only query the D-08 hard-block needs is a **single-row lookup** ("was `username`/`photo` changed <1h ago on THIS sender") — a JSONB field on the already-loaded `Sender` row answers it with zero extra joins/queries. `_load_sender_by_slug` already fetches the row.
- **No audit/history is required** (explicitly deferred). A dedicated history table only pays off when you need an append-only trail — which CONTEXT.md rules out.
- Precedent in-repo: cooldown/rest state is tracked as a **scalar timestamp column** on `senders` (`checker_rest_until`, `restricted_until`) — a JSONB map is the natural per-field generalization of that same "state, not log" approach.
- The seed itself suggests "храним `last_profile_change_at` per-field ... на `Sender`" — a JSONB map is the minimal realization.

Reject the dedicated history table for this phase (it's the deferred audit-log idea in disguise). If audit is ever wanted, add it later as a separate append-only table (mirror `sender_restriction_events`).

## Common Pitfalls

### Pitfall 1: ORM `server_default` drift on the new NOT NULL JSONB column
**What goes wrong:** `profile_field_changed_at JSONB NOT NULL` added in the migration but the ORM column lacks `server_default` → `create_all` (tests / fresh DB / recovery) builds the column without a DB default → any raw-SQL INSERT that omits it (test factories like `_insert_sender_raw`) hits `NotNullViolation`.
**Why:** `create_all` uses ORM metadata, not the migration file (documented repeatedly: mig 040/042, memory note).
**How to avoid:** `server_default=text("'{}'::jsonb")` on the ORM column, mirroring the migration DEFAULT exactly. Nullable columns are fine as-is.
**Warning sign:** `test_senders.py::_insert_sender_raw` fails with NotNull on the new column.

### Pitfall 2: `edit_2fa(email=...)` requires a synchronous `email_code_callback` — incompatible with stateless per-op clients (CRITICAL)
**What goes wrong:** Verified live — `client.edit_2fa` signature is `(current_password=None, new_password=None, *, hint='', email=None, email_code_callback=None)`. Its docstring: *"If an email is provided, a callback that returns the code sent to it must also be set, else it raises `ValueError`."* The callback is invoked **during** the `edit_2fa` call and must return the code Telegram just emailed. The project's `TelegramService` creates a client per-operation and disconnects immediately — a single HTTP request cannot block while the user reads their inbox.
**Why:** Setting a recovery email is inherently interactive (Telegram sends a confirmation code out-of-band).
**How to avoid — split the 2FA flow into two paths:**
- **Password set/change (one request):** `await client.edit_2fa(current_password=<or None>, new_password=<new>)` with **no `email`** → completes synchronously, no callback. Covers D-04 set-new (current=None) and change (current=required).
- **Recovery email (two requests, raw functions):**
  1. *Start:* `pwd = await client(GetPasswordRequest())`; `srp = compute_check(pwd, current_password)`; `await client(UpdatePasswordSettingsRequest(password=srp, new_settings=PasswordInputSettings(email=<new_email>)))`. Telegram emails a code and this raises `EmailUnconfirmedError` (carries `code_length`). Catch it → return `{"status": "EMAIL_CONFIRMATION_SENT", "code_length": n}`. (The pending-email state now lives **server-side on Telegram**, keyed to the account — not to the client connection.)
  2. *Confirm:* second request on a **fresh** per-op client: `await client(ConfirmPasswordEmailRequest(code=<user_code>))`.
Because the pending-confirmation is account-level, the disconnect-between-requests pattern is preserved. No client state needs persisting; the only thing to track (optional) is a UI hint that a confirmation is pending.
**Warning sign:** `ValueError: Requested a change of the recovery email but no email_code_callback` at runtime; or the request hanging.

### Pitfall 3: Leaking the photo blob as a public URL / inlining it in list JSON
**What goes wrong:** Returning a raw file path or public URL for the avatar, or base64-embedding the blob in `SenderResponse`, violates D-11 and bloats the frequently-rendered account list.
**How to avoid:** Serve via a workspace-scoped `GET /senders/{slug}/photo` returning `Response(content=blob, media_type=mime)` behind `auth_dep`; list responses carry only `has_photo: bool`.
**Warning sign:** blob bytes appearing in `/senders` list JSON; photo endpoint reachable without a JWT.

### Pitfall 4: Username pre-check vs. no-op vs. taken
**What goes wrong:** Treating `CheckUsernameRequest`==False as always "taken", or letting `UsernameNotModifiedError` (submitting the current username) surface as a 500.
**How to avoid:** Pre-check with `CheckUsernameRequest`; on the actual `UpdateUsernameRequest` catch `UsernameOccupiedError`→taken, `UsernameInvalidError`→bad format, `UsernameNotModifiedError`→treat as success (no change), `UsernamePurchaseAvailableError`→paid handle. Clearing the username = `UpdateUsernameRequest(username="")`.
**Warning sign:** 500 when a user re-submits their existing username.

### Pitfall 5: `PasswordTooFreshError` / `SessionTooFreshError` on young accounts
**What goes wrong:** Immediately after setting a 2FA password (or on a session that logged in very recently), Telegram blocks adding/changing the recovery email or resets for a waiting period, raising `PasswordTooFreshError`/`SessionTooFreshError` (carry a seconds value).
**Why:** Telegram anti-hijack cooldown.
**How to avoid:** Catch both → 409 `TOO_FRESH` with the seconds, surface a "try again in N" message. This intersects D-09 (accounts <7 days) — the warmup/age warning modal is a natural place to pre-empt it.
**Warning sign:** email-set step fails on a freshly-onboarded or freshly-password-set account.

### Pitfall 6: Photo delete needs the current photo's `InputPhoto` (file_reference)
**What goes wrong:** `DeletePhotosRequest` needs `List[InputPhoto]` with a valid `file_reference`, which expires. Building an `InputPhoto` from stale data fails.
**How to avoid:** Fetch fresh in the same op: `photos = await client.get_profile_photos('me', limit=1)` (or `GetUserPhotosRequest(user_id='me', offset=0, max_id=0, limit=1)`), then `await client(DeletePhotosRequest(id=[photos[0]]))` — Telethon converts the `Photo` to `InputPhoto` via `utils.get_input_photo`. Do the fetch+delete in one client session.
**Warning sign:** `FILE_REFERENCE_EXPIRED` / invalid photo errors.

## Code Examples

### Example 1: Update name / bio (verified signatures)
```python
# Source: telethon.tl.functions.account.UpdateProfileRequest — sig verified live in api container
from telethon.tl.functions.account import UpdateProfileRequest
# Only pass fields the user changed; None leaves the field untouched.
await client(UpdateProfileRequest(first_name="Ivan", last_name="Petrov", about="Sales @ Acme"))
# raises AboutTooLongError (>70 free / >140 premium), FirstNameInvalidError, FloodWaitError
```

### Example 2: Username pre-check + set (verified)
```python
from telethon.tl.functions.account import CheckUsernameRequest, UpdateUsernameRequest
available = await client(CheckUsernameRequest("acme_sales"))   # -> bool
if available:
    await client(UpdateUsernameRequest("acme_sales"))
# UpdateUsernameRequest("") clears the username.
# except: UsernameOccupiedError / UsernameInvalidError / UsernameNotModifiedError / UsernamePurchaseAvailableError
```

### Example 3: Upload profile photo from uploaded bytes (verified)
```python
# FastAPI: photo arrives as UploadFile (multipart) — mirror knowledge_bases.upload_document
from telethon.tl.functions.photos import UploadProfilePhotoRequest
import io
raw = await file.read()                      # size/mime validated first (see Example router)
input_file = await client.upload_file(io.BytesIO(raw), file_name=file.filename or "avatar.jpg")
await client(UploadProfilePhotoRequest(file=input_file))
# except: PhotoCropSizeSmallError, PhotoExtInvalidError, FloodWaitError
```

### Example 4: Delete current profile photo (verified)
```python
from telethon.tl.functions.photos import DeletePhotosRequest
photos = await client.get_profile_photos('me', limit=1)     # fresh file_reference
if photos:
    await client(DeletePhotosRequest(id=[photos[0]]))        # Telethon -> InputPhoto
```

### Example 5: 2FA password set/change (one request) + recovery-email start/confirm (two requests)
```python
# --- Password only: ONE request, no callback needed ---
await client.edit_2fa(current_password=None, new_password="NewPass1!")      # set (D-04 no-2fa-yet)
await client.edit_2fa(current_password="OldPass", new_password="NewPass1!") # change (D-04 has-2fa)
# except: PasswordHashInvalidError (wrong current) -> reuse onboarding PASSWORD_INVALID code

# --- Recovery email: TWO requests via raw functions (see Pitfall 2) ---
from telethon.tl.functions.account import (GetPasswordRequest,
    UpdatePasswordSettingsRequest, ConfirmPasswordEmailRequest)
from telethon.tl.types.account import PasswordInputSettings
from telethon.password import compute_check
from telethon.errors import EmailUnconfirmedError
# Step 1 (request A):
pwd = await client(GetPasswordRequest())
srp = compute_check(pwd, current_password)          # current_password required (2FA already on)
try:
    await client(UpdatePasswordSettingsRequest(
        password=srp,
        new_settings=PasswordInputSettings(email=new_email)))
except EmailUnconfirmedError as e:
    return {"status": "EMAIL_CONFIRMATION_SENT", "code_length": e.code_length}
# Step 2 (request B, fresh per-op client):
await client(ConfirmPasswordEmailRequest(code=user_entered_code))
```

### Example 6: Manual resync — fetch live profile into cache (D-12, verified)
```python
from telethon.tl.functions.users import GetFullUserRequest
me = await client.get_me()                                   # -> User: .username, .first_name, .last_name
full = await client(GetFullUserRequest('me'))                # -> .full_user.about (bio)
photo_bytes = await client.download_profile_photo('me', file=bytes)   # -> bytes | None (verified)
# then UPDATE senders SET tg_username=me.username, tg_bio=full.full_user.about,
#                        tg_photo=photo_bytes, tg_photo_mime='image/jpeg'
```

### Example 7: Multipart photo upload endpoint (mirror knowledge_bases.upload_document)
```python
# Source: app/routers/knowledge_bases.py:317-362
from fastapi import UploadFile, File
MAX_PHOTO_BYTES = 5 * 1024 * 1024   # generous inbound cap; Telegram re-encodes server-side
ALLOWED_MIME = {"image/jpeg", "image/png"}

@router.post("/senders/{slug}/photo", response_model=SenderResponse)
async def upload_sender_photo(slug: str, file: UploadFile = File(...),
                              ctx: AuthCtx = Depends(auth_dep), db: AsyncSession = Depends(get_db)):
    sender = await _load_sender_by_slug(db, ctx, slug)
    raw = await file.read()
    if len(raw) > MAX_PHOTO_BYTES:
        raise HTTPException(413, detail={"code": "FILE_TOO_LARGE", "message": f"Max {MAX_PHOTO_BYTES} bytes"})
    if file.content_type not in ALLOWED_MIME:
        raise HTTPException(422, detail={"code": "UNSUPPORTED_FILE_TYPE", "message": "jpg/png only"})
    # 1h hard-block check (D-08) on profile_field_changed_at['photo'] BEFORE the Telegram call
    # ... call telegram_service.set_profile_photo(...) then cache raw + stamp timestamp ...
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Seed PROF-01 excluded 2FA/email ("security-critical, separate flow") | In scope this phase (D-01), password via `edit_2fa`, email via raw two-request flow | 2026-07-03 (this phase) | Bigger scope; the email flow is the non-trivial part |
| Card "Update" = refresh status | "Update" = manual profile resync (`GetFullUser`); status derives fresh on every load | 2026-07-03 (D-12) | Old "refresh status" affordance dropped |
| No cached profile on senders | username/bio/photo cached on `Sender`, refreshed on edit + onboarding | 2026-07-03 (D-10) | List reads are cache-only |

**Deprecated/outdated:** the seed's `account.setPrivacy` row is explicitly **deferred** (not this phase). The seed's "block editing during warmup" is **rejected** (D-09 → warn-only).

## Open Questions

1. **Recovery-email confirmation flow — one endpoint with pending-state, or two explicit endpoints?**
   - What we know: `edit_2fa(email=)` can't run in a single stateless request (Pitfall 2). The raw two-request flow works because the pending-email state is account-side on Telegram. `ConfirmPasswordEmailRequest(code)` needs only the code (no SRP), so request B can use a fresh client.
   - What's unclear: UX/state modeling — does the frontend hold "confirmation pending" state, or does the backend persist a flag on the sender? D-04 says current password is required to *start*; the confirm step does not need it again.
   - Recommendation: two explicit endpoints — `POST /senders/{slug}/2fa/recovery-email` (start → `EMAIL_CONFIRMATION_SENT` + `code_length`) and `POST /senders/{slug}/2fa/recovery-email/confirm` (`{code}`). Keep password change on a separate `POST /senders/{slug}/2fa` (set/change, no email). No server-side pending flag required; the frontend drives the two steps. Planner to confirm the masked-vs-re-enter behavior (Claude's Discretion item).

2. **Bio length cap: 70 vs 140.** Free accounts cap bio at 70 chars; Telegram Premium at 140. `AboutTooLongError` is the runtime guard regardless.
   - Recommendation: Pydantic `max_length=70` on the field for the common case; rely on `AboutTooLongError → 400 BIO_TOO_LONG` as the backstop for premium mismatch. (Column sized 140 to be safe.)

3. **Photo compression/normalization before caching.** D-11 expects ≤~200KB square avatars, but the inbound upload could be larger. Telegram re-encodes server-side, but our *cache* stores what we choose.
   - Recommendation: cache the bytes returned by `download_profile_photo('me', file=bytes)` *after* upload+resync (Telegram's own normalized small avatar) rather than the raw uploaded file — smaller, already square-ish, matches what other clients see. Avoids adding Pillow just to resize (though Pillow is already transitively present via `qrcode[pil]` if resizing is ever wanted). Confirm with planner.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Telethon | all profile TL calls | ✓ | 1.42.0 (verified `pip show` in `api`) | — |
| python-multipart | photo `UploadFile` | ✓ | 0.0.6 | — |
| PostgreSQL (BYTEA/JSONB) | photo + cooldown storage | ✓ | 16 (prod) | — |
| Live Telegram MTProto + valid sessions | every edit/resync op | ✓ | in continuous use (senders/listener) | account with dead session → existing `SessionAuthError → 403` path |
| Pillow (optional, for resize) | photo normalization (OQ 3) | ✓ (transitive via `qrcode[pil]==7.4.2`) | — | cache Telegram's normalized avatar instead of resizing (recommended) |

No missing dependencies. This phase is code + one idempotent migration on top of already-deployed infrastructure. Live Telegram is intrinsic (as in all sender operations); a sender with an expired session surfaces the existing `SessionAuthError → 403 AUTH_ERROR` path — the edit form should route the user to the existing reauth flow (D-13).

## Validation Architecture

*(nyquist_validation = true in .planning/config.json → section included.)*

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.x + pytest-asyncio (`asyncio_mode=auto`, session-scoped loop) |
| Config file | `pyproject.toml` (`[tool.pytest.ini_options]`) |
| Quick run command | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_senders.py tests/test_account_profile.py -x` |
| Full suite command | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest` |

**CRITICAL (CLAUDE.md + memory `feedback_pytest_drop_schema_prod`):** tests run **ONLY** through the test-overlay. NEVER `docker compose run --rm api pytest` without `-f docker-compose.test.yml` (DATABASE_URL → prod, conftest DROP SCHEMA). NEVER `down -v` (wipes prod volume). The conftest guard blocks the bare form but the overlay is the correct path.

**Mocking pattern (verified in `tests/test_onboarding.py`):** Telethon is mocked via `monkeypatch.setattr` on the client factory, with `AsyncMock` clients (`client.connect`, `client.get_me`, `client.sign_in`, etc.). For profile tests, mock `TelegramService` methods (or the `client(...)` request dispatch + `edit_2fa`/`upload_file`/`download_profile_photo`) with `AsyncMock`; assert on the TL request type dispatched (same style as Phase 17's request-type introspection). API-level integration tests bootstrap a workspace via JWT (`_create_workspace_via_jwt`) and insert senders via `_insert_sender_raw` (reuse from `test_senders.py`).

### Phase Requirements → Test Map
| Proposed Req | Behavior | Test Type | Automated Command | File Exists? |
|--------------|----------|-----------|-------------------|-------------|
| PROF-01 | new columns present; ORM/migration parity; raw INSERT omitting JSONB succeeds (server_default) | integration | `pytest tests/test_account_profile.py::test_profile_columns_defaults -x` | ❌ Wave 0 |
| PROF-02 | name/bio update dispatches `UpdateProfileRequest`; AboutTooLong → 400 | unit | `pytest tests/test_account_profile.py::test_update_name_bio -x` | ❌ Wave 0 |
| PROF-03 | username pre-check + set; taken→400, not-modified→ok; 1h hard-block enforced | unit+integration | `pytest tests/test_account_profile.py::test_username -x` | ❌ Wave 0 |
| PROF-04 | photo upload dispatches upload_file+UploadProfilePhoto; delete path; 1h hard-block; size/mime validation | integration | `pytest tests/test_account_profile.py::test_photo -x` | ❌ Wave 0 |
| PROF-05 | password set/change via edit_2fa; wrong pwd→PASSWORD_INVALID; email start→EMAIL_CONFIRMATION_SENT; confirm | unit | `pytest tests/test_account_profile.py::test_2fa -x` | ❌ Wave 0 |
| PROF-06 | resync updates cached fields from GetFullUser/get_me/download_profile_photo | integration | `pytest tests/test_account_profile.py::test_resync -x` | ❌ Wave 0 |
| PROF-07 | photo-serve endpoint returns bytes+mime, requires auth, workspace-scoped 404 | integration | `pytest tests/test_account_profile.py::test_photo_serve_auth -x` | ❌ Wave 0 |
| PROF-08 | onboarding finalize populates cache (username at minimum) | integration | `pytest tests/test_onboarding.py::test_finalize_caches_profile -x` | extend existing |
| D-08 guardrail | username/photo change <1h ago → hard block (save disabled equivalent = 409/422) | unit | `pytest tests/test_account_profile.py::test_cooldown_block -x` | ❌ Wave 0 |
| D-09 guardrail | warmup / <7-day account → warning surfaced, NOT blocked | unit | `pytest tests/test_account_profile.py::test_warmup_advisory_not_blocking -x` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `... run --rm api pytest tests/test_account_profile.py -x`
- **Per wave merge:** `... run --rm api pytest tests/test_senders.py tests/test_account_profile.py tests/test_onboarding.py`
- **Phase gate:** full suite green before `/gsd:verify-work` (baseline is GREEN per memory `project-test-baseline-red`; ~896+ collected).

### Wave 0 Gaps
- [ ] `tests/test_account_profile.py` — new file covering PROF-01..07 + D-08/D-09 guardrails (RED scaffold, deferred in-body imports to keep `--collect-only` clean, per Phase 13/16/17/18 convention)
- [ ] Extend `tests/test_onboarding.py` — finalize caches profile fields (PROF-08)
- [ ] Mock helper for Telethon profile ops (AsyncMock `client(...)` dispatch + `edit_2fa`/`upload_file`/`download_profile_photo`) — model on `tests/test_onboarding.py::_make_mock_client`

## Sources

### Primary (HIGH confidence)
- Live introspection in the running `api` container (Telethon 1.42.0): `edit_2fa` signature + docstring, `UpdateProfileRequest`/`UpdateUsernameRequest`/`CheckUsernameRequest`/`UploadProfilePhotoRequest`/`DeletePhotosRequest`/`GetUserPhotosRequest`/`GetFullUserRequest` constructor signatures, `download_profile_photo`/`upload_file`/`get_profile_photos` signatures + docstrings, `compute_check`/`PasswordInputSettings` signatures, and presence of all listed `telethon.errors` classes.
- `requirements.txt` — `telethon==1.42.0`, `python-multipart==0.0.6`, `pgvector`, FastAPI/SQLAlchemy pins.
- Codebase read directly: `app/services/telegram.py` (per-op client pattern, `send_message_by_telegram_id`, error mapping), `app/routers/senders.py` (CRUD, `auth_dep`, `_load_sender_by_slug`, spambot-check try/finally shape), `app/routers/onboarding.py` (`verify_2fa`, `_map_telethon_error`, `_finalize_onboarding_or_reauth` get_me hook), `app/routers/knowledge_bases.py` (multipart `UploadFile` upload), `app/models/__init__.py` (`Sender` columns, `KbDocument.raw_content` LargeBinary, server_default usage), `app/schemas/__init__.py` (`SenderResponse`/`SenderUpdate`), `migrations/035_*.sql` + `046_*.sql` (idempotent ADD COLUMN pattern), `app/config.py` (Settings knob pattern), `tests/test_senders.py` + `tests/test_onboarding.py` (test + mocking patterns), `.planning/config.json`, `.planning/seeds/account-profile-self-serve.md`.

### Secondary (MEDIUM confidence)
- Telethon docstrings for `edit_2fa` email-callback behavior and `download_profile_photo(file=bytes)` return type (authoritative, but behavior around `EmailUnconfirmedError` in the raw path is inferred from MTProto semantics + Telethon source, not executed against live Telegram in this research).

### Tertiary (LOW confidence) — flag for validation during implementation
- The exact **raw two-request recovery-email flow** (`UpdatePasswordSettingsRequest` raising `EmailUnconfirmedError` then `ConfirmPasswordEmailRequest` on a fresh client) is derived from Telethon's own `edit_2fa` implementation + MTProto docs; it has NOT been executed end-to-end against a live account in this research. **Validate against a real test account early in the phase** (this is the riskiest technical assumption — Open Question 1 / Pitfall 2).
- `PasswordTooFreshError`/`SessionTooFreshError` waiting-period durations for young accounts are Telegram-server-side and not documented as fixed values — handle defensively (surface the returned seconds).

## Metadata

**Confidence breakdown:**
- Standard stack (Telethon API surface, error types): **HIGH** — every symbol and signature verified live in the deployed container.
- Architecture patterns (per-op client, BYTEA serving, multipart, migration, cooldown JSONB): **HIGH** — all mirror existing, read-in-repo patterns.
- 2FA password-only path: **HIGH** — `edit_2fa` signature/docstring verified.
- 2FA recovery-email two-request path: **MEDIUM-LOW** — mechanism sound and grounded in verified signatures, but not executed against live Telegram; flagged as the phase's key validation target.
- Pitfalls: **HIGH** for 1/3/4/5/6, **HIGH** for the *existence* of the Pitfall-2 constraint (callback verified), MEDIUM for the recommended workaround.

**Research date:** 2026-07-03
**Valid until:** ~2026-08-03 (stable — Telethon pinned at 1.42.0; Telegram profile TL surface is slow-moving. Re-verify only if Telethon is upgraded.)
