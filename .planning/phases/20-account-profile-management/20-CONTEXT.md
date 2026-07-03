# Phase 20: Account Profile Management - Context

**Gathered:** 2026-07-03
**Status:** Ready for planning

<domain>
## Phase Boundary

Editable Telegram account profile (name, username, bio, photo, 2FA password + recovery email) from the account edit view, plus richer account cards on the accounts list page (photo, name, username, phone, update/delete/reauth actions). (ROADMAP.md Phase 20, depends on Phase 19.)

This phase promotes and extends `.planning/seeds/account-profile-self-serve.md` (PROF-01, planted 2026-05-27, originally scoped for v2). The seed excluded 2FA/email as "security-critical, separate flow" — the user has now explicitly requested both be included in this phase.

</domain>

<decisions>
## Implementation Decisions

### Editable field scope
- **D-01:** Full scope per user request: first/last name, username, bio (about), profile photo (upload + delete), 2FA password (set/change), recovery email. This supersedes the seed's original exclusion of 2FA/email.
- **D-02:** Telegram has no standalone "account email" concept — the only email in the system is the 2FA recovery email, set/changed together with the 2FA password via `account.updatePasswordSettings` (Telethon: `client.edit_2fa(current_password=..., new_password=..., new_recovery_email=...)`).

### 2FA change flow
- **D-03:** User types the current 2FA password manually in the form every time a change is submitted (mirrors native Telegram UX) — the platform never stores the 2FA password anywhere (not even encrypted). Only the Telegram session string is encrypted/stored, as today.
- **D-04:** If the account has no 2FA set yet, the form flow is "set new" (no current-password field). If already set, current password is required before accepting a new password and/or recovery email.
- **D-05:** Lost/forgotten current password → out of scope for in-app recovery; user must go through Telegram's own recovery-email flow outside the platform.

### Anti-spam guardrail (frequency limiting)
- **D-06:** Reuse the seed's warning-modal pattern before saving profile changes, with per-field frequency tracking (`last_profile_change_at`-style, per field).
- **D-07:** Name and bio changes → warning only, never blocked (Telegram doesn't hard-limit these; just advisory).
- **D-08:** Username and photo changes → **hard block** (not just warning) if the same field was changed less than 1 hour ago. Save button stays disabled with a countdown/message; this is the aggressive case the seed specifically called out.
- **D-09:** Accounts in `lifecycle_status='warmup'` or younger than 7 days (from `senders.created_at`) → the same warning modal additionally mentions warmup/age, but editing is **not blocked** — advisory only, consistent with D-07.

### Data source & caching strategy
- **D-10:** Cache profile fields (username, bio, photo) on the `Sender` row. Populate at onboarding finalize and refresh after every successful profile edit. Account list reads are cache-only (fast, no per-render Telegram round-trip).
- **D-11:** Photo storage: BYTEA column in Postgres (small square avatar, compressed, expected ≤~200KB) — no object storage/CDN exists in this project yet. Served through a platform API endpoint (same pattern as encrypted session handling — data stays server-side, never exposed as a raw blob URL).
- **D-12:** The "Update" action on an account card is repurposed as **manual resync**: it re-fetches the live Telegram profile (`GetFullUser`) and refreshes the cached username/bio/photo — for cases where the profile was changed manually via the native Telegram client outside the platform. It does NOT open the edit form (edit form has its own entry point) and does NOT relate to the old "refresh status" affordance, which the user confirmed is redundant since status already derives fresh on every page load.

### Account list card updates
- **D-13:** Cards display: photo (cached avatar or fallback initials), name, username, phone, plus existing actions (delete, reauth) plus the new "Update"/resync action (D-12). Reauth and delete already exist in the codebase (`app/routers/senders.py`, onboarding flow) — this phase only adds the new profile fields to the card and the resync action, not new delete/reauth logic.
- **D-14:** Clicking a card / an explicit "Edit" action opens the full profile edit form (name/username/bio/photo/2FA/email) — distinct from the card-level "Update" resync button (D-12).

### Claude's Discretion
- Exact DB schema for per-field frequency tracking (JSONB `{field: timestamp}` column on `Sender` vs. a small dedicated history table) — planner decides based on query patterns needed for the 1h-block check.
- Whether to persist a change-history log for audit purposes — not requested; only the minimal frequency-check state is required.
- Exact Telethon call sequence and error handling for `UpdateProfileRequest`, `UpdateUsernameRequest`, `UploadProfilePhotoRequest`, `DeletePhotosRequest`, `client.edit_2fa(...)` — researcher/planner to confirm current Telethon API surface and error types (e.g. `UsernameOccupiedError`, `FloodWaitError`, `PasswordHashInvalidError`).
- Exact wording/thresholds display in the warning modal (seed gives the tone: "не чаще 1 раза в день для username", "не чаще раза в неделю для фото" — this phase's hard limit is narrower: 1h block, not a full day/week block, per D-08).
- Migration file numbering (next available: `047_...`, following `046_telegram_service_status.sql`).
- Photo upload endpoint request shape (multipart vs base64) and max upload size validation.
- Whether recovery-email is exposed as read-only (masked) if already set without going through a change, vs. always requiring a full re-enter.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Original design source (promoted from seed)
- `.planning/seeds/account-profile-self-serve.md` — original scope, UX guardrail wording, dependencies, open questions. This phase supersedes its v2 exclusion of 2FA/email (see D-01) but keeps its warning-modal UX and per-field cooldown tracking approach (D-06).

### Sender model & existing account endpoints
- `app/models/__init__.py` (`Sender` class, ~line 74) — current columns: no username/bio/photo yet; `lifecycle_status`, `created_at`, `restriction_status` used for D-09 warmup/age check
- `app/routers/senders.py` — existing CRUD pattern (list/get/PATCH/DELETE), workspace-scoped via `auth_dep`, `SenderResponse`/`SenderUpdate` schemas to extend
- `app/schemas/__init__.py` (`SenderResponse`, ~line 121) — response schema to extend with cached profile fields

### Telethon integration patterns
- `app/services/telegram.py` (`TelegramService` class, ~line 254) — "client created per-operation, disconnected after use" pattern MUST be followed for all new profile-edit calls (persistent connections steal listener updates)
- `app/routers/onboarding.py` (`verify_2fa`, ~line 623) — existing pattern for Telethon 2FA password sign-in (`client.sign_in(password=...)`) — the closest existing analog for the new 2FA-change flow, though `edit_2fa` is a different Telethon call

### Migrations & conventions
- `migrations/046_telegram_service_status.sql` — most recent migration; next is `047_...`
- `CLAUDE.md` (repo root of tg-outreach) — raw-SQL idempotent migration pattern, async-everywhere, test-overlay-only pytest, ORM `server_default` must mirror DB defaults (memory: ORM default vs server_default drift)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- **TelegramService per-operation client pattern** (`get_client`/`disconnect_client` in `app/services/telegram.py`) — all new profile-edit Telethon calls slot into this existing lifecycle, zero changes to the connection-management approach
- **Onboarding 2FA sign-in flow** (`app/routers/onboarding.py::verify_2fa`) — shows the established pattern for handling Telegram password prompts and `PASSWORD_INVALID`-style error responses; the new 2FA-change endpoint should mirror this error-handling shape
- **Existing delete/reauth actions** on senders — already implemented, only need card-level UI wiring for the new fields, not new backend logic

### Established Patterns
- Sender fields as VARCHAR/JSONB + server_default, migrations idempotent (`IF NOT EXISTS`)
- Workspace-scoped endpoints via `Depends(auth_dep)` — new profile-edit endpoints follow the same auth pattern as `app/routers/senders.py`
- Encrypted-at-rest sensitive data pattern (Fernet on `session_string`) — informs D-11's decision to keep photo bytes server-side only, never exposed as a public blob URL

### Integration Points
- `app/routers/senders.py` — add profile-edit endpoints (PATCH-style for name/username/bio, dedicated endpoints for photo upload/delete and 2FA change) alongside existing sender CRUD
- `app/models/__init__.py` (`Sender`) — new columns: username, bio, photo (BYTEA), profile field change timestamps (D-10, D-11, frequency tracking per Claude's Discretion)
- `lovable-handoff/openapi.json` — regenerate after API changes (Lovable frontend contract)
- Frontend repo `/root/apps/aimly/aimly-tg-outreach` — account edit view (new form) + account list card updates (photo/username/phone display + resync action)

</code_context>

<specifics>
## Specific Ideas

- User explicitly overrode the seed's conservative v2 scope to include 2FA and recovery email now — this is a deliberate scope expansion beyond what was originally planted, not an oversight.
- The "Update" button on cards was initially assumed to mean "refresh status," but status already derives fresh on every page load (making a manual refresh redundant) — repurposed as a manual Telegram resync for out-of-band profile changes (D-12).
- Guardrail severity is asymmetric by design: name/bio are low-risk (warning only), username/photo are the fields Telegram actually polices aggressively (hard 1h block).

</specifics>

<deferred>
## Deferred Ideas

- Privacy settings (who can see phone/photo/last_seen via `account.setPrivacy`) — seed marked this "low priority"; not requested in this phase's scope, remains a candidate for a future phase.
- Change-history/audit log of profile edits — seed's open question, not requested here; only the minimal frequency-check state is in scope.
- Bulk "apply this avatar to all my accounts" operation — seed's open question, explicitly not part of this phase's per-account edit flow.
- Blocking profile edits during warmup entirely (vs. the warning-only approach chosen here) — considered and rejected (D-09).

</deferred>

---

*Phase: 20-account-profile-management*
*Context gathered: 2026-07-03*
