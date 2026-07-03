# Phase 20: Account Profile Management - Discussion Log

**Date:** 2026-07-03

## Areas Discussed

### 2FA and email scope
- **Options presented:** Include both (email = 2FA recovery) / Exclude 2FA+email (defer to future phase) / 2FA yes, email view-only
- **Selected:** Include both, email = recovery for 2FA
- **Notes:** Seed originally excluded these as security-critical/separate-flow. User explicitly overrode that for this phase.

### Anti-spam guardrail approach
- **Options presented:** Warning modal + soft frequency limits / Warning only, no blocking / No guardrail this phase
- **Selected:** Warning modal + soft-limits by frequency (with follow-up narrowing to hard block for username/photo)

### Data source for cards/edit form
- **Options presented:** Cache in DB, refresh on read/after edits / Always live-fetch via Telethon / Hybrid (cache for list, live-fetch on edit open)
- **Selected:** Cache in DB, refresh on read/after edits

### "Update" action semantics on account card
- **Options presented:** Sync profile from Telegram / Opens edit form / Both, as separate actions
- **Selected (custom):** User clarified original intent was "refresh status," but noted status already refreshes on every page load, making that redundant — repurposed as "resync cached profile from Telegram" for cases where profile changed via native client.

### Warmup/young account handling
- **Options presented:** Warning only, don't block / Hard-block editing during warmup / No restriction at all
- **Selected:** Warning only, don't block

### Photo storage
- **Options presented:** BYTEA in Postgres / Local disk volume + path in DB / Don't cache photo, text fields only
- **Selected:** BYTEA in Postgres

### Hard-block threshold for username/photo
- **Options presented:** Hard 1h block for username+photo, warning-only for name/bio / Warning everywhere, no hard block
- **Selected:** Hard 1h block for username+photo

### Current 2FA password handling
- **Options presented:** Manual re-entry every time (never stored) / Store encrypted like session
- **Selected:** Manual re-entry every time, never stored

## Deferred Ideas

- Privacy settings (`account.setPrivacy`) — seed low-priority item, not in this phase's scope
- Change-history/audit log of profile edits — not requested
- Bulk "apply avatar to all accounts" — seed open question, not in scope
- Hard-blocking edits during warmup entirely — considered, rejected in favor of warning-only

## Claude's Discretion Items

- DB schema shape for per-field frequency tracking (JSONB vs dedicated table)
- Telethon call sequencing/error handling details (UpdateProfileRequest, UploadProfilePhotoRequest, edit_2fa, etc.)
- Exact warning modal copy/thresholds display
- Migration numbering (047+)
- Photo upload request shape (multipart vs base64) and size limits
- Recovery email read-only masking vs. full re-entry requirement

---

*Phase: 20-account-profile-management*
*Discussion logged: 2026-07-03*
