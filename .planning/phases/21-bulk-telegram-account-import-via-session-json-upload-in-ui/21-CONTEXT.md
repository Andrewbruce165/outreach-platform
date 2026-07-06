# Phase 21: Bulk Telegram account import via session JSON upload in UI - Context

**Gathered:** 2026-07-06
**Status:** Ready for planning

<domain>
## Phase Boundary

Import already-authorized Telegram accounts into a workspace by uploading vendor-format **pairs** `<phone>.json` + `<phone>.session` through the UI, with bulk (multi-account) support — bypassing the phone/SMS onboarding flow. The `.session` is a live Telethon SQLite session (auth_key present) that must be converted to our encrypted StringSession storage; the `.json` carries the client fingerprint + optional proxy/2FA.

**In scope:** ZIP upload endpoint, pair matching, SQLite→StringSession conversion, per-account fingerprint capture, encrypted 2FA storage, validation (connect + get_me), dedup, background job + status polling, per-file result report, proxy assignment.

**Out of scope:** phone/SMS/QR onboarding changes (unchanged), profile editing (Phase 20 owns this), SpamBot restriction probing at import (reconcile handles it later), bulk profile editing (backlog Phase 999.1).

</domain>

<decisions>
## Implementation Decisions

### Client fingerprint & API credentials
- **D-01:** Store the **device fingerprint per-account** from the vendor JSON on the `senders` row. Mapping JSON→Telethon: `device` → `device_model`, `sdk` → `system_version`, `app_version` → `app_version`, `lang_code` → `lang_code`, `system_lang_code` → `system_lang_code`. `lang_pack` stays `"tdesktop"` (matches JSON already; also matches the current global patch).
- **D-02:** `make_telegram_client` gains an **optional per-sender fingerprint override**. Imported senders connect with their stored fingerprint (en-US, app 6.8.2, etc.); the global `_CLIENT_FINGERPRINT` (`app/services/telegram.py:152`, app 5.3.1 / ru) remains the **fallback** for the 13 existing phone-onboarded senders that have no stored fingerprint. Fallback must be strict — a NULL fingerprint column → behave exactly as today (no regression to the working pool).
- **D-03:** **Do NOT store `app_id`/`app_hash` per-account.** The vendor's `app_id=2040` is the public Telegram Desktop id (identical across all vendor accounts — nothing to "carry"). The vendor confirmed empirically that reconnecting his sessions under a *different* app_id does not log them out → MTProto `auth_key` survives an app_id change (it lives at the DC level; app_id rides in `initConnection` over the established key). Imported sessions therefore keep connecting under our **global** `telegram_api_id` (`3273…`), same as the current 13. Rationale: per-account app_id columns give ~zero benefit for maximum schema/complexity cost.
- **D-04:** The real risk the fingerprint decision fixes is the **ru/en locale mismatch** on US (+1) vendor numbers (a weak antifraud signal, not a logout). `lang_pack` (the one field that actually triggers session termination on mobile logout when empty) is already `"tdesktop"` everywhere, so the base is protected.

### 2FA password handling
- **D-05:** **Store the 2FA password from JSON, encrypted (Fernet), in a new nullable `senders` column** — same at-rest pattern as `session_string`. This is a **deliberate deviation from Phase 20 D-03** (which forbids ever persisting the 2FA password). The deviation is scoped to **imported accounts only** and justified by a different use-case: Phase 20 is self-serve editing of one's *own* single account (manual password entry is fine); bulk import of purchased accounts (hundreds) makes manual per-account entry unworkable, and the client explicitly wants the platform to retrieve it when needed.
- **D-06:** Phase 20's profile-edit form, for imported accounts, **auto-fills the current 2FA password from the stored (decrypted) value** — no manual entry required for imported senders. (Phase 21 writes the column; the profile-form read path is the Phase 20 integration point — see code_context.)
- **D-07:** Security trade-off noted for the planner: the DB already holds `session_string` (full account access) encrypted, so adding the 2FA password raises the blast radius of a DB leak (full account takeover) but does not change the fact that the DB already contains account-access secrets. Encryption at rest is mandatory; the plaintext password is never logged or returned in API responses.

### Bulk upload UX
- **D-08:** Delivery format = **single ZIP archive**. Client uploads one ZIP of all `<phone>.json` + `<phone>.session` pairs; the backend unzips and matches `.json`↔`.session` by **basename** (the `session_file` field in the JSON = the shared basename, e.g. `+18646884306`). One POST — avoids a hundreds-part multipart form and is simpler for the Lovable-generated frontend.
- **D-09:** Batch processing is **asynchronous**: the upload POST creates an import job, returns a `job_id` immediately, and the frontend **polls a status endpoint** for progress (processed/total + per-file status). Rationale: connect + get_me per account is ~1–3s each; a synchronous POST would hit the nginx/HTTP timeout on a large batch.
- **D-10:** **Per-file result report** with partial success: each pair resolves to `ok` or `failed` + reason. A broken/invalid/unpaired file **must not fail the whole batch** — it is reported and the rest continue.

### Validation & start state
- **D-11:** Import validation per account: convert the SQLite Telethon session → StringSession, `connect()` → `is_user_authorized()` → `get_me()` to populate `phone` / `telegram_id` / `tg_username` / name. **No @SpamBot probe at import** — `restriction_status` defaults to `'none'` and the periodic reconcile loop picks up any restriction later. (Accepted risk: a restricted account may briefly sit in the sending pool until reconcile runs.)
- **D-12:** **Storage format ≠ delivery format.** We store the encrypted Telethon **StringSession** in `senders.session_string` (`encrypt_session()` from `app/services/encryption.py`). Import converts: load the vendor SQLite `.session` with Telethon → `client.session.save()` / `StringSession.save(...)` → `encrypt_session` → DB. Telethon 1.42.0 supports this directly.
- **D-13:** Imported accounts start in **`lifecycle_status = 'active'`** (ready to work). Accepted risk noted for the planner: a freshly imported account can enter sending without a warmup period — this is the client's deliberate choice; surface warmth/age advisory in the accounts UI if cheap, but do not block.
- **D-14:** **Dedup by `telegram_id`** (from `get_me()`): if the same tg_id is already connected in the workspace → **skip + report** "already connected" (consistent with the CSV contact-import behavior, Phase 2 D-03). Do not overwrite the existing sender's session.
- **D-15:** **Proxy: JSON → else pool.** If the JSON `proxy` field is set, write it to `senders.proxy`; if `null` (the common case), assign a free proxy from the workspace `ProxyPool` (existing behavior).

### Claude's Discretion
- Exact import-job / status schema shape and the per-file report structure (D-09/D-10).
- Where to stage the `.session` bytes on disk for Telethon SQLiteSession conversion (it reads a file), and cleanup after.
- Exact new column names on `senders` (fingerprint fields, encrypted 2FA).
- ZIP size limit and max accounts per batch.
- Whether fingerprint fields live as discrete columns vs a single JSONB blob (either is fine as long as `make_telegram_client` can build the override).

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Phase-specific grounded analysis
- `.planning/phases/21-bulk-telegram-account-import-via-session-json-upload-in-ui/21-NOTES.md` — full file analysis of the vendor sample (JSON schema, .session SQLite parse, storage-vs-delivery gap, fingerprint-mismatch risk, open questions). The single most important ref for this phase.

### Telegram client & session handling
- `app/services/telegram.py` — `make_telegram_client()` (~line 233), `_CLIENT_FINGERPRINT` / `DESKTOP_CLIENT_KWARGS` (~line 152, the fingerprint to make per-account-overridable per D-02), `build_proxy_tuple()` (proxy dict → Telethon tuple, reuse for D-15)
- `app/services/encryption.py` — `encrypt_session()` (Fernet); the pattern to reuse for the new encrypted 2FA column (D-05)
- `app/routers/onboarding.py` — `_create_sender_from_session()` (~line 295) and `_finalize_onboarding_or_reauth()` (~line 264): the existing "session → sender row" path the import must mirror (populate profile cache per Phase 20 D-10)

### Data model
- `app/models/__init__.py` — `Sender` class (~line 74; add fingerprint + encrypted-2FA columns), `ProxyPool` class (~line 479; free-proxy assignment for D-15)

### Cross-phase decisions this phase touches
- `.planning/phases/20-account-profile-management/20-CONTEXT.md` — **D-03** (2FA never persisted — Phase 21 D-05 deliberately deviates for imported accounts), **D-10** (profile cache on Sender: username/bio/photo — import should populate it), **D-04** (2FA set/change flow that D-06 auto-fills)
- `.planning/phases/02-tg-accounts-contacts/02-CONTEXT.md` — **D-03** (skip+report dedup pattern, mirrored by D-14), **D-22** (ProxyPool workspace-scoped, used by D-15)

### Operational conventions (CLAUDE.md)
- `/root/apps/aimly/tg-outreach/CLAUDE.md` — "Async pipeline / migrations only raw SQL NNN_name.sql, auto-applied, idempotent"; "Telethon entity-cache cold start" (imported .session entities table is empty — cold start applies); "Restriction Audit" (reconcile picks up restrictions per D-11); ORM `server_default` mandatory for new NOT NULL columns (memory `project-orm-default-vs-server-default-drift`)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `encrypt_session()` (`app/services/encryption.py`) — reuse verbatim for the new encrypted 2FA column (D-05); same Fernet key/pattern as `session_string`.
- `make_telegram_client()` (`app/services/telegram.py`) — add an optional per-sender fingerprint override param (D-02); every worker (queue/listener/checker/warmup/onboarding/reauth) builds clients through it, so the fallback must be behavior-preserving.
- `build_proxy_tuple()` — converts a proxy dict to Telethon format; reuse for both JSON-supplied and pool-assigned proxies (D-15).
- `_create_sender_from_session()` / onboarding finalize path (`app/routers/onboarding.py`) — the canonical "authorized session → sender row" flow, including profile-cache population (Phase 20 D-10); the import path should mirror it rather than reinvent.
- `ProxyPool` free-proxy assignment (existing) — for D-15's fallback.
- CSV import skip+report semantics (Phase 2) — the model for D-14's dedup report.

### Established Patterns
- Fernet-at-rest for secrets (`session_string`); new 2FA column follows it (D-05/D-07).
- Migrations: raw SQL `migrations/NNN_short_name.sql`, idempotent, auto-applied at api start; **`server_default` mandatory** on new NOT NULL columns (create_all builds the test/fresh-DB schema from the ORM, not the migration — memory `project-orm-default-vs-server-default-drift`).
- Everything workspace-scoped (workspace_id FK on senders / proxy_pool).
- Telethon 1.42.0 (matches vendor session-format v7).

### Integration Points
- `senders` table — new columns: per-account fingerprint (device_model/system_version/app_version/lang_code/system_lang_code, or one JSONB), encrypted 2FA password.
- `make_telegram_client` signature — the fingerprint override is the seam that makes imported sessions connect with their own fingerprint (D-02).
- **Phase 20 profile-edit form** — read path for the stored 2FA password (D-06); this is the one place Phase 21's new column is consumed downstream. Planner should decide whether the read wiring lands in Phase 21 or is flagged for Phase 20 follow-up.
- Periodic reconcile loop — will pick up restrictions on imported accounts post-import (D-11).

</code_context>

<specifics>
## Specific Ideas

- Vendor sample lives (gitignored) in `/root/apps/aimly/tg-outreach/scratchpad/` — `+18646884306.json` + `+18646884306.session`. Use it as the smoke-test fixture (never commit the `.session` — it holds a live auth_key).
- Vendor's stated practice ("I don't carry app_id/app_hash, I just load the sessions and it works") is the empirical basis for D-03 — treat it as verified: app_id changes don't kill authorized sessions.

</specifics>

<deferred>
## Deferred Ideas

- **@SpamBot restriction probe at import** — considered, rejected for this phase (D-11); the periodic reconcile handles restriction detection. Could be added later if active-start proves risky in practice.
- **Bulk account profile editing** — already parked as backlog Phase 999.1; out of scope here.
- **Warmup-first start for imported accounts** — the safer alternative to active-start (D-13) was offered and declined; if freshly-imported accounts get flagged, revisit a warmup or paused default.

</deferred>

---

*Phase: 21-bulk-telegram-account-import-via-session-json-upload-in-ui*
*Context gathered: 2026-07-06*
