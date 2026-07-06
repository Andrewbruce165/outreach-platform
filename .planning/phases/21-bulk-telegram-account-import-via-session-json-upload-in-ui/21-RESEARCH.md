# Phase 21: Bulk Telegram account import via session JSON upload in UI - Research

**Researched:** 2026-07-06
**Domain:** Telethon session conversion, bulk file ingest (ZIP), encrypted secret storage, per-account client fingerprinting, async job + status polling
**Confidence:** HIGH (all critical claims verified against the running codebase and the real vendor sample; the SQLite→StringSession conversion recipe was executed successfully on `+18646884306.session` with zero network)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Client fingerprint & API credentials**
- **D-01:** Store the device fingerprint **per-account** from the vendor JSON on the `senders` row. Mapping JSON→Telethon: `device` → `device_model`, `sdk` → `system_version`, `app_version` → `app_version`, `lang_code` → `lang_code`, `system_lang_code` → `system_lang_code`. `lang_pack` stays `"tdesktop"`.
- **D-02:** `make_telegram_client` gains an **optional per-sender fingerprint override**. Imported senders connect with their stored fingerprint; the global `_CLIENT_FINGERPRINT` (`app/services/telegram.py:152`, app 5.3.1 / ru) remains the **strict fallback** for the 13 existing phone-onboarded senders that have no stored fingerprint. NULL fingerprint column → behave exactly as today (no regression).
- **D-03:** **Do NOT store `app_id`/`app_hash` per-account.** Imported sessions keep connecting under the **global** `telegram_api_id`. app_id changes do not kill an authorized session (auth_key lives at the DC level; app_id rides in `initConnection` over the established key — vendor-confirmed empirically).
- **D-04:** The real risk the fingerprint decision fixes is the **ru/en locale mismatch** on US (+1) vendor numbers (weak antifraud signal, not a logout). `lang_pack` (the field that triggers session termination when empty) is already `"tdesktop"` everywhere.

**2FA password handling**
- **D-05:** Store the 2FA password from JSON, **encrypted (Fernet)**, in a new nullable `senders` column — same at-rest pattern as `session_string`. Deliberate deviation from Phase 20 D-03, scoped to imported accounts only.
- **D-06:** Phase 20's profile-edit form, for imported accounts, **auto-fills the current 2FA password** from the stored (decrypted) value. Phase 21 writes the column; the profile-form read path is the Phase 20 integration point.
- **D-07:** Security trade-off for the planner: DB already holds `session_string` (full account access) encrypted, so adding the 2FA password raises the blast radius of a DB leak. Encryption at rest is mandatory; plaintext password is never logged or returned in API responses.

**Bulk upload UX**
- **D-08:** Delivery format = **single ZIP archive**. Backend unzips and matches `.json`↔`.session` by **basename** (the `session_file` field in the JSON = the shared basename, e.g. `+18646884306`).
- **D-08a:** **Two-step flow with a backend preview.** Step 1 (preview): POST the ZIP → unzip + match pairs + validate JSON schema (NO Telegram connect) → returns a recognized-set summary **without importing**. Step 2 (confirm): client picks a role and confirms → launches the actual import. Requires **server-side staging** of the unpacked ZIP between the two steps, keyed by a preview id/token, with TTL cleanup (Claude's Discretion on mechanism).
- **D-09:** The **confirm step is asynchronous**: creates an import job, returns a `job_id` immediately, frontend **polls a status endpoint** (processed/total + per-file status). Preview/step 1 is fast (unzip + parse only) → can be synchronous.
- **D-10:** **Per-file result report** with partial success: each pair resolves to `ok` or `failed` + reason. A broken/invalid/unpaired file **must not fail the whole batch**.

**Role assignment**
- **D-16:** The imported accounts' **role (`sender` | `checker`) is chosen once for the whole batch** at step 2 and applied to every account in the ZIP (`senders.role`). Per-account role selection declined.
- **D-17:** **Checker import = the same import path + `role='checker'`** — no special handling. Proxy, fingerprint capture, encrypted-2FA storage, dedup, SQLite→StringSession conversion identical for both roles.

**Validation & start state**
- **D-11:** Import validation per account: convert SQLite session → StringSession, `connect()` → `is_user_authorized()` → `get_me()` to populate `phone`/`telegram_id`/`tg_username`/name. **No @SpamBot probe at import** — `restriction_status` defaults to `'none'`; the periodic reconcile loop picks up restrictions later.
- **D-12:** **Storage format ≠ delivery format.** Store the encrypted Telethon **StringSession** in `senders.session_string` (`encrypt_session()`). Import converts: load vendor SQLite `.session` → `StringSession.save(...)` → `encrypt_session` → DB. Telethon 1.42.0 supports this directly.
- **D-13:** Imported accounts start in **`lifecycle_status = 'active'`** (ready to work). Accepted risk: no warmup before sending. Surface a warmth/age advisory in the accounts UI if cheap, but do not block.
- **D-14:** **Dedup by `telegram_id`** (from `get_me()`): same tg_id already in the workspace → **skip + report** "already connected". Do not overwrite the existing sender's session.
- **D-15:** **Proxy: JSON → else pool.** If JSON `proxy` is set → write to `senders.proxy`; if `null` → assign a free proxy from the workspace `ProxyPool`.

### Claude's Discretion
- Exact import-job / status schema shape and the per-file report structure (D-09/D-10).
- Preview-staging mechanism and TTL between step 1 and step 2 (D-08a) — how the unpacked ZIP is held server-side, keyed, and cleaned up.
- Where to stage the `.session` bytes on disk for Telethon SQLiteSession conversion, and cleanup after.
- Exact new column names on `senders` (fingerprint fields, encrypted 2FA).
- ZIP size limit and max accounts per batch.
- Whether fingerprint fields live as discrete columns vs a single JSONB blob (either is fine as long as `make_telegram_client` can build the override).

### Deferred Ideas (OUT OF SCOPE)
- **@SpamBot restriction probe at import** — rejected for this phase (D-11); periodic reconcile handles restriction detection.
- **Bulk account profile editing** — parked as backlog Phase 999.1.
- **Warmup-first start for imported accounts** — declined in favor of active-start (D-13).
- **Per-account role selection in a mixed batch** — declined (D-16); role is one choice per ZIP.
- Phone/SMS/QR onboarding changes (unchanged), profile editing (Phase 20 owns this).
</user_constraints>

## Summary

This phase adds a bulk import path that bypasses phone/SMS onboarding: the client uploads one ZIP of `<phone>.json` + `<phone>.session` vendor pairs, the backend matches them by basename, converts each live Telethon SQLite session into our encrypted StringSession storage, captures a per-account client fingerprint so reconnects don't trip Telegram antifraud, and creates one `senders` row per authorized account. The mechanics are almost entirely a **recomposition of assets that already exist** in the codebase — there is very little new invention required.

The single highest-risk technical claim (D-12: SQLite `.session` → encrypted StringSession with no network login) was **verified empirically** against the real vendor sample inside the running api container: `SQLiteSession('/tmp/imp.session')` loads locally, exposes `dc_id=1` and a 256-byte `auth_key`, and `StringSession.save(sqlite_session)` produces a valid `1A`-prefixed StringSession that round-trips back to the same auth_key — all with zero Telegram traffic. This is the same string format `encrypt_session()` already stores. The conversion is safe and offline.

The three code seams the phase touches are all well-understood: (1) `make_telegram_client()` / `TelegramService.get_client()` gain an optional fingerprint override (D-02) — a low-risk additive param with a strict NULL-fallback that preserves the working 13-account pool; (2) `_create_sender_from_session()` in the onboarding router is the canonical "authorized session → sender row" flow to mirror (including PROF-08 profile-cache population and the dedup-by-slug upsert that already implements D-14); (3) a new async job worker modeled on `KnowledgeIngestWorker` (Phase 16) drives step-2 import with pending→processing→ok/failed status polling.

**Primary recommendation:** Reuse, don't reinvent. Convert sessions with the verified `StringSession.save(SQLiteSession(path))` recipe; store fingerprint + Fernet-encrypted 2FA in `senders` via idempotent migration **051** (ORM `server_default` mandatory); thread the fingerprint through `get_client` alongside the already-passed `sender.proxy`; model the async import job on `KnowledgeIngestWorker` + a `KbDocument`-style status row; reuse the `csv_imports` BYTEA + `expires_at` staging pattern for the ZIP preview; and mirror `_create_sender_from_session`'s slug-based upsert for dedup (D-14 falls out for free because `slug = sender-{telegram_id}` is already UNIQUE per workspace).

## Phase Requirements (proposed — not yet in ROADMAP)

No requirement IDs were mapped to this phase (the orchestrator flagged "TBD in ROADMAP"). The planner should formalize a family in REQUIREMENTS.md before/at plan time. Proposed set derived from the locked decisions, for the planner to adopt/rename:

| Proposed ID | Description | Decisions |
|-------------|-------------|-----------|
| IMPT-01 | ZIP upload preview endpoint: unzip + pair `.json`↔`.session` by basename + validate JSON schema, no Telegram connect; return recognized/unpaired/malformed summary + staging token | D-08, D-08a |
| IMPT-02 | Confirm endpoint launches an async import job (returns `job_id`); role chosen once for the whole batch | D-09, D-16 |
| IMPT-03 | Per-account import: SQLite `.session` → StringSession → `encrypt_session` → `senders.session_string`; connect + `is_user_authorized` + `get_me` to populate phone/tg_id/username/name | D-11, D-12 |
| IMPT-04 | Capture per-account fingerprint from JSON (device/sdk/app_version/lang_code/system_lang_code); `make_telegram_client` optional override; global stays strict fallback | D-01, D-02, D-03, D-04 |
| IMPT-05 | Store 2FA password Fernet-encrypted in a new nullable `senders` column; never logged/returned | D-05, D-07 |
| IMPT-06 | Dedup by telegram_id (skip + report "already connected"); proxy from JSON else free ProxyPool entry | D-14, D-15 |
| IMPT-07 | Per-file result report with partial success; imported accounts start `lifecycle_status='active'`, `restriction_status='none'` | D-10, D-13 |
| IMPT-08 | Idempotent migration 051 (fingerprint + encrypted-2FA columns) with ORM `server_default` mirror | D-01, D-05 |
| IMPT-09 | Frontend two-step bulk-import UI (ZIP upload → preview → role radio → confirm → progress poll) + openapi/types regen (sibling repo) | D-08, D-08a, D-09, D-16 |
| IMPT-10 | Phase 20 profile-form read path auto-fills stored 2FA password for imported accounts (planner decides Phase 21 vs Phase 20 follow-up) | D-06 |

## Standard Stack

### Core (all already in the project — no new dependencies)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Telethon | 1.42.0 (pinned in `requirements.txt`) | Load vendor SQLite session, convert to StringSession, connect + `get_me` | Already the project's MTProto client; vendor session-format v7 is compatible |
| cryptography (Fernet) | via `app/services/encryption.py` | Encrypt session_string + 2FA at rest | The one encryption code path already used for `session_string` and BYOK keys |
| FastAPI `UploadFile` / `File(...)` | current | Multipart ZIP upload | Same mechanism already used by `POST /contacts/import/preview` and KB file upload |
| Python `zipfile` (stdlib) | 3.11 | Unzip + enumerate members in-memory | No third-party lib needed; guard against zip-bombs (see Pitfalls) |
| SQLAlchemy 2.0 async + asyncpg | current | DB writes, staging rows, job status | Project standard; async everywhere |
| Pydantic v2 | current | Vendor-JSON schema validation, request/response models | Project standard |

### Supporting (existing helpers to reuse verbatim)
| Asset | Location | Purpose |
|-------|----------|---------|
| `encrypt_session()` / `decrypt_session()` | `app/services/encryption.py` | Reuse verbatim for `session_string` and the new encrypted-2FA column (D-05) |
| `make_telegram_client()` | `app/services/telegram.py:233` | Add optional `fingerprint` param (D-02) |
| `TelegramService.get_client()` | `app/services/telegram.py:291` | Thread `sender.fingerprint` through, alongside the already-passed `proxy` |
| `build_proxy_tuple()` | `app/services/telegram.py:170` | Proxy dict → Telethon tuple; reuse for JSON-supplied and pool-assigned proxies (D-15) |
| `_create_sender_from_session()` | `app/routers/onboarding.py:295` | The canonical "session → sender row" path to mirror (slug, PROF-08 cache, upsert) |
| `_resolve_proxy()` / ProxyPool assignment | `app/routers/onboarding.py:135` + `ProxyPool` model | Free-proxy selection for D-15 fallback |
| `KnowledgeIngestWorker` | `app/services/kb_ingest_worker.py` | Template for the async import-job worker (D-09) |
| `KbDocument` status model | `app/models/__init__.py:776` | Template for a per-item status row (pending/processing/indexed/failed → ok/failed) |
| `CsvImport` BYTEA + `expires_at` | `app/models/__init__.py:588` + `contacts.py:301` | Template for ZIP preview staging with TTL (D-08a) |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Async job worker (D-09) | Synchronous confirm | Rejected in CONTEXT (nginx/HTTP timeout on connect+get_me ~1-3s/account × N) |
| DB-blob staging (`csv_imports` pattern) | On-disk temp dir keyed by token | Both viable (discretion). DB-blob survives api restart + is workspace-scoped + auto-cleans via expiry-on-read; on-disk needs its own cleanup. See Open Questions. |
| Discrete fingerprint columns | Single JSONB `fingerprint` column | Either fine (D discretion). JSONB is fewer migration lines + `make_telegram_client(**fingerprint)` maps cleanly; discrete columns are queryable. Recommend JSONB. |

**Installation:** None. All dependencies already present.

**Version verification:** `requirements.txt` pins `telethon==1.42.0`; the running api container executed the conversion recipe successfully (host has 1.43.2 — the `StringSession.save(session)` / `SQLiteSession(path)` API is identical and stable across 1.42–1.43). No new packages.

## Architecture Patterns

### Recommended flow (mirrors existing two-step + async-worker patterns)
```
Step 1 — PREVIEW (synchronous, fast):
  POST /api/v1/accounts/import/preview  (multipart ZIP)
    → unzip in-memory (zipfile), enumerate members
    → match <base>.json ↔ <base>.session by basename (session_file field)
    → validate each JSON against a Pydantic vendor schema (NO Telegram connect)
    → stage the raw ZIP bytes in a BYTEA row (like csv_imports) with expires_at TTL
    → return { import_id, matched:[...], unpaired:[...], malformed:[...] }

Step 2 — CONFIRM (async):
  POST /api/v1/accounts/import/{import_id}/confirm  { role: 'sender'|'checker' }
    → validate role, re-read staged ZIP (check expires_at, 410 if gone)
    → create ONE import-job row + N per-file item rows (status='pending')
    → return { job_id } immediately (202)
  AccountImportWorker (lifespan, mirrors KnowledgeIngestWorker):
    claim one pending item FOR UPDATE SKIP LOCKED → 'processing' (commit)
    → write .session bytes to a temp path → SQLiteSession → StringSession.save
    → make_telegram_client(StringSession, proxy, fingerprint) → connect
    → is_user_authorized → get_me → dedup-by-slug → INSERT sender (or skip+report)
    → item status 'ok' | 'failed' + reason; delete temp file
  GET /api/v1/accounts/import/{job_id}/status  → { processed, total, items:[...] }
```

### Pattern 1: Offline SQLite → StringSession conversion (VERIFIED)
**What:** Load the vendor `.session` SQLite file and re-serialize its dc_id + auth_key as a StringSession, with no network.
**When:** Once per account during step-2 import, before `connect()`.
**Verified example (executed on the real vendor sample in the api container):**
```python
# Source: Telethon official "Session Files" docs + empirically confirmed 2026-07-06
from telethon.sessions import SQLiteSession, StringSession

# Telethon's SQLiteSession appends ".session" only if the arg lacks it — passing
# the full path WITH the extension is used as-is (both were tested). Stage the
# vendor bytes to a temp file first (Telethon reads a file, not bytes).
sqlite_sess = SQLiteSession("/tmp/imp.session")   # loads locally, no connect
# sqlite_sess.dc_id -> 1 ; sqlite_sess.auth_key.key -> 256 bytes
string = StringSession.save(sqlite_sess)          # '1A…' 353-char StringSession
sqlite_sess.close()
# store it exactly like an onboarded session:
encrypted = encrypt_session(string)               # app/services/encryption.py
```
Actual output observed: `dc_id: 1`, `auth_key present: True len: 256`, `StringSession prefix: 1A total_len: 353`, `roundtrip dc: 1 auth_match: True`.

### Pattern 2: Per-sender fingerprint override seam (D-02, strict fallback)
**What:** `make_telegram_client` merges a per-account fingerprint over the global default; NULL → identical to today.
**Example:**
```python
# app/services/telegram.py — additive param, behavior-preserving when fingerprint is None
def make_telegram_client(session, proxy=None, flood_sleep_threshold=60,
                         client_class=TelegramClient, fingerprint: dict | None = None):
    fp = {**_CLIENT_FINGERPRINT, **(fingerprint or {})}   # None -> exactly _CLIENT_FINGERPRINT (D-02)
    client = client_class(
        session, settings.telegram_api_id, settings.telegram_api_hash,   # api_id stays GLOBAL (D-03)
        flood_sleep_threshold=flood_sleep_threshold,
        proxy=build_proxy_tuple(proxy), **fp,
    )
    client._init_request.lang_pack = "tdesktop"          # already tdesktop everywhere (D-04) — do NOT drop
    return client
```
JSON→kwargs mapping (D-01): `{"device_model": device, "system_version": sdk, "app_version": app_version, "lang_code": lang_code, "system_lang_code": system_lang_code}`. Keep vendor `app_version` verbatim (`"6.8.2 x64"`); `device="KVM"`; `system_version="Windows 10 x64"`.

### Pattern 3: Dedup by telegram_id ≡ dedup by slug (D-14 for free)
**Key insight:** `_create_sender_from_session` already computes `slug = f"sender-{telegram_id}"`, and `idx_senders_workspace_slug` is UNIQUE on `(workspace_id, slug)`. So dedup-by-telegram_id is exactly a "does `sender-{tg_id}` already exist in this workspace?" SELECT. For import (D-14 = skip + report, NOT overwrite), do NOT reuse the onboarding upsert; instead SELECT-first and if present → report "already connected" and continue. The existing upsert is a re-auth contract; import must NOT clobber a live session.

### Pattern 4: Async job + status polling (mirror KnowledgeIngestWorker)
Claim one pending item `FOR UPDATE SKIP LOCKED`, flip to `processing` in a committed transaction so the UI poll sees progress, do the network work outside the claim TX, then write terminal status in a fresh TX. The worker loop must never die on a per-item error (log + continue). Register `start()`/`stop()` in `app/main.py` lifespan next to the other 7 workers.

### Anti-Patterns to Avoid
- **Overwriting an existing sender's session on dedup collision** — D-14 is skip+report. Only the re-auth flows overwrite.
- **Dropping the `lang_pack = "tdesktop"` patch** when adding the fingerprint override — it is the field that terminates sessions when empty (D-04). Keep it unconditionally.
- **Storing app_id/app_hash per-account** — explicitly rejected (D-03); public + identical + fragments the pool + adds hot-path risk.
- **Persisting the 2FA password in plaintext or returning it in any API response / log** — Fernet-at-rest only (D-05/D-07).
- **A single failed/broken pair aborting the batch** — every item is independent (D-10).
- **Holding a Telethon connection open after import** — persistent connections steal updates from the listener (see `TelegramService` docstring). Connect → get_me → disconnect per account.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| SQLite session → portable session | Manual SQLite reads + struct packing of auth_key | `StringSession.save(SQLiteSession(path))` | Verified offline recipe; Telethon owns the byte format + versioning |
| Encrypt session / 2FA at rest | New Fernet setup or a second key | `encrypt_session()` / `decrypt_session()` | One ENCRYPTION_KEY, one code path (Phase 18 already aliased it for BYOK) |
| Proxy dict → Telethon tuple | Custom tuple assembly | `build_proxy_tuple()` | Handles socks5/4/http + auth already |
| "Authorized session → sender row" | New INSERT logic | Mirror `_create_sender_from_session()` | Gets slug derivation, PROF-08 profile cache, IntegrityError race handling for free |
| Background job with progress | Threads / ad-hoc tasks | Mirror `KnowledgeIngestWorker` + `KbDocument` status row | Proven claim/commit/never-die loop already in lifespan |
| Two-step upload staging with TTL | Redis / disk cache | `csv_imports` BYTEA + `expires_at` (expiry-on-read) | Survives api restart, workspace-scoped, no new infra |
| Free-proxy assignment | New pool logic | `ProxyPool` + `_resolve_proxy` | Workspace-scoped assignment already exists |

**Key insight:** This phase is ~90% recomposition. The only genuinely new artifacts are (a) the vendor-JSON Pydantic schema, (b) two staging/job tables + one worker, (c) migration 051 columns, and (d) the fingerprint override seam. Everything else has a verbatim-reusable precedent.

## Common Pitfalls

### Pitfall 1: Fingerprint mismatch on reconnect (the phase's headline risk)
**What goes wrong:** Reconnecting an imported session with our hardcoded ru-locale, app 5.3.1 fingerprint (different from the en-US, app 6.8.2 client that created it) is a weak antifraud signal on +1 numbers.
**Why:** `_CLIENT_FINGERPRINT` is applied to ALL clients today.
**How to avoid:** D-01/D-02 — store + apply the per-account fingerprint. `lang_pack` is already `tdesktop` everywhere so the account-termination vector is already closed; the fingerprint fix is about the locale/version antifraud signal, not a logout.
**Warning signs:** Imported +1 accounts getting security-flagged / re-login prompts shortly after first connect.

### Pitfall 2: Fingerprint threading blast radius (regression risk to the working 13)
**What goes wrong:** `make_telegram_client` and `get_client` are called from many hot paths (queue/listener/checker/warmup + 11 internal `TelegramService` methods + `senders.py`). A non-strict default could change behavior for phone-onboarded senders.
**Why:** `get_client(sender_slug, sender_id, encrypted_session, proxy)` does not currently carry a fingerprint; every caller already reads the sender row (they pass `sender.proxy`).
**How to avoid:** Add `fingerprint: dict | None = None` to both functions; callers pass `sender.fingerprint` alongside `sender.proxy`. NULL fingerprint → `{**_CLIENT_FINGERPRINT}` verbatim (D-02 strict fallback). Add a regression test asserting a NULL-fingerprint client is byte-identical to today. Call sites to update: `queue.py:879`, `warmup.py:714`, `senders.py:882`, `listener.py:1439`, checker's `_get_client` (`checker.py:218/240`), and the internal `TelegramService` methods that pass through `get_client` (they already receive the sender's data from callers — thread it once at the `get_client` signature).

### Pitfall 3: ORM `server_default` drift on new NOT NULL columns
**What goes wrong:** A new NOT NULL column added only in the migration (not the ORM) breaks the test/fresh-DB schema, because `init_db` runs `Base.metadata.create_all` (ORM) BEFORE `_apply_migrations`. Raw INSERTs omitting the column then hit NotNullViolation.
**Why:** Documented repeatedly (mig 040/042/049; memory `project-orm-default-vs-server-default-drift`).
**How to avoid:** The new fingerprint + 2FA columns should be **nullable** (they are: NULL fingerprint = use global fallback; NULL 2FA = no stored password), so this is low-risk here — but if any new column is NOT NULL, it MUST carry a matching ORM `server_default`. Mirror every migration-051 column in the `Sender` ORM class.

### Pitfall 4: Telethon SQLiteSession filename handling
**What goes wrong:** `SQLiteSession(session_id)` appends `.session` if the arg doesn't already end with it. Passing a basename like `+18646884306` yields `+18646884306.session`; passing a full path with the extension is used as-is (both tested OK). A path mismatch → Telethon silently creates a NEW empty session file (auth_key=None) instead of reading the vendor one.
**How to avoid:** Write the vendor `.session` bytes to a known temp path ending in `.session` and pass that exact path; assert `sqlite_sess.auth_key is not None` immediately after load (empty auth_key = wrong file / not a real session → fail the item, don't connect).
**Warning signs:** `is_user_authorized()` returns False for a session the vendor swears is live → you loaded an empty/wrong file.

### Pitfall 5: Connecting during import steals updates from the listener
**What goes wrong:** Leaving imported clients connected competes with the listener container for MTProto updates.
**How to avoid:** Per-account: connect → `is_user_authorized` → `get_me` → **disconnect immediately** (same discipline as `TelegramService.get_client` callers). Never keep the import client alive. The listener picks up the new sender on its reconcile loop (same as onboarding, D-18).

### Pitfall 6: Entity-cache cold start on first real use
**What goes wrong:** The vendor `.session` has `entities: 0 rows` (verified) — the entity cache is empty. First send/resolve by telegram_id can raise `ValueError: Could not find the input entity`.
**Why:** Documented in CLAUDE.md "Telethon entity-cache cold start".
**How to avoid:** Not this phase's job to fix (existing `send_message_by_telegram_id` already does the `get_dialogs(200)` warm-up fallback). Note it for the planner so import validation (get_me only) isn't expected to warm the cache.

### Pitfall 7: ZIP safety (bomb / path traversal / huge batch)
**What goes wrong:** A malicious or huge ZIP exhausts memory/disk; member names with `../` escape the temp dir.
**How to avoid:** Enforce a ZIP byte limit + max-accounts-per-batch (discretion — suggest e.g. ≤50 MB, ≤500 accounts); read `ZipInfo.file_size` before extracting; use only the basename of each member (never the archived path) for pairing and for the temp filename; reject members with absolute/`..` paths.

### Pitfall 8: Dedup race under concurrent imports / double-submit
**What goes wrong:** Two overlapping imports (or a double-clicked confirm) INSERT the same `sender-{tg_id}` and violate `idx_senders_workspace_slug`.
**How to avoid:** Wrap the INSERT in the same IntegrityError-recovery the onboarding path uses (`_create_sender_from_session` lines 382-403): catch IntegrityError, re-SELECT, report "already connected" for the loser. The worker's `FOR UPDATE SKIP LOCKED` claim prevents two workers taking the same item.

### Pitfall 9: Secrets in logs / API responses
**What goes wrong:** Logging the decrypted session, the 2FA password, or the raw StringSession leaks full account access.
**How to avoid:** Log only slug/tg_id/phone-prefix (mirror existing `phone[:6]***` masking). The 2FA column and session_string are never in any response body or log line (D-07). The staged ZIP BYTEA and temp `.session` files hold live auth_keys — clean up temp files in a `finally`, and let staging rows expire.

## Code Examples

### Migration 051 (idempotent, JSONB fingerprint recommended)
```sql
-- 051: Bulk account import — per-account client fingerprint + encrypted 2FA (Phase 21).
-- Next free migration number is 051 (050 is latest). Auto-applied at api start by
-- app/database.py::_apply_migrations in lexical order; MUST be idempotent.
-- Both columns NULLABLE: NULL fingerprint = use global _CLIENT_FINGERPRINT fallback (D-02);
-- NULL twofa = no stored password. Mirror BOTH on the Sender ORM class.
ALTER TABLE senders ADD COLUMN IF NOT EXISTS client_fingerprint JSONB NULL;
ALTER TABLE senders ADD COLUMN IF NOT EXISTS twofa_password_enc TEXT  NULL;  -- Fernet ciphertext, same as session_string
```
(If the planner prefers discrete columns instead of JSONB: `device_model VARCHAR`, `system_version VARCHAR`, `app_version VARCHAR`, `lang_code VARCHAR`, `system_lang_code VARCHAR` — all NULL. JSONB is fewer lines and maps directly to `make_telegram_client(**fingerprint)`.)

### Vendor JSON schema (from the verified real sample)
```python
# Real sample field set (all present in +18646884306.json):
# app_id(2040), app_hash, device("KVM"), sdk("Windows 10 x64"),
# app_version("6.8.2 x64"), system_lang_pack("en-US"), system_lang_code("en-US"),
# lang_pack("tdesktop"), lang_code("en"), twoFA(null), role(""), id(null),
# phone(null), username(null), ..., proxy(null), ipv6(false), session_file("+18646884306")
class VendorAccountJson(BaseModel):
    session_file: str                       # REQUIRED — shared basename, the pairing key
    device: str | None = None               # -> device_model
    sdk: str | None = None                   # -> system_version
    app_version: str | None = None           # -> app_version
    lang_code: str | None = None
    system_lang_code: str | None = None
    twoFA: str | None = None                 # -> Fernet-encrypt into twofa_password_enc (D-05)
    proxy: dict | None = None                # -> senders.proxy if set, else pool (D-15)
    # app_id / app_hash intentionally IGNORED (D-03); id/phone/username filled from get_me (D-11)
```
Note: real vendor records leave `id/phone/username/first_name/last_name` null → these MUST come from `get_me()` (D-11); the phone is also recoverable from the filename basename as a fallback.

### Dedup + create (import variant — skip, don't overwrite)
```python
me = await client.get_me()
tg_id = me.id
slug = f"sender-{tg_id}"                     # same derivation as onboarding
existing = (await db.execute(select(Sender).where(
    Sender.slug == slug, Sender.workspace_id == ctx.workspace_id))).scalars().first()
if existing is not None:
    return item_result(status="failed", reason="already_connected")   # D-14 skip+report
# else INSERT mirroring _create_sender_from_session, plus:
#   role=batch_role (D-16), client_fingerprint=<mapped JSON>, twofa_password_enc=encrypt_session(twoFA) if twoFA,
#   proxy=json_proxy or await _assign_free_proxy(...), lifecycle_status='active', restriction_status='none',
#   tg_username=me.username  (PROF-08 cache)
```

## State of the Art

| Old Approach | Current Approach | When | Impact |
|--------------|------------------|------|--------|
| SQLite `.session` files on disk (Telethon default) | Encrypted StringSession in DB | Established in this project | Import must convert; storage format ≠ delivery format (D-12) |
| One global `_CLIENT_FINGERPRINT` for all accounts | Per-account fingerprint with global fallback | This phase | Imported accounts connect as their creating client (D-02) |
| Onboarding via phone/SMS/QR only | + bulk session import | This phase | Bypasses interactive onboarding for purchased accounts |

**Deprecated/outdated:** Nothing to remove. The old `telegram-api`/`outreach-platform` projects are stopped (CLAUDE.md) — do not touch them.

## Open Questions

1. **Staging mechanism: DB BYTEA vs on-disk temp (D-08a discretion)**
   - What we know: `csv_imports` uses a BYTEA blob + `expires_at`, checked on read (no active sweep worker — only `onboarding_sessions` has a cleanup worker). It survives api restart and is workspace-scoped.
   - What's unclear: ZIPs of many `.session` files may be larger than CSVs (each session ~28 KB; 500 accounts ≈ 14 MB + JSONs — well within a BYTEA/DB row, but consider a size cap).
   - Recommendation: Reuse the `csv_imports` BYTEA + `expires_at` pattern (a new `account_import_stagings` table) with expiry-on-read; optionally extend `onboarding_cleanup_worker` (or add a sweep) to hard-delete expired staging rows so the DB doesn't accumulate live-auth_key blobs. Prefer DB over disk so the auth_key secrets aren't left on the filesystem longer than the per-account temp file needs.

2. **2FA read-path ownership (D-06 / IMPT-10)**
   - What we know: Phase 21 writes `twofa_password_enc`; Phase 20's profile form is the consumer.
   - What's unclear: Whether the auto-fill read wiring lands in this phase or a Phase 20 follow-up.
   - Recommendation: Planner decides. Writing the column (Phase 21) is independent; the read wiring is a small Phase 20 form change and can be flagged as a follow-up task if it risks scope creep.

3. **Temp file for SQLite conversion — location + cleanup**
   - Recommendation: Write to a per-item unique path under the OS temp dir (or a mounted scratch), suffixed `.session`, `chmod 600`, delete in a `finally`. Never leave vendor `.session` bytes on disk after the item completes.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Telethon | Session conversion + connect + get_me | ✓ (verified in api container) | 1.42.0 pinned | — |
| Fernet / cryptography | Encrypt session + 2FA | ✓ (`encryption.py` in use) | current | — |
| Python `zipfile` (stdlib) | Unzip ZIP | ✓ | 3.11 | — |
| PostgreSQL 16 | Staging + job + senders | ✓ | 16 | — |
| Telegram DC reachability (DC1 149.154.175.53:443) via proxy | connect() validation at import | assumed ✓ (prod already connects 13 accounts) | — | If a proxy is unreachable, that item fails with a clear reason (D-10) — batch continues |
| Vendor sample fixture | Smoke test | ✓ | `scratchpad/+18646884306.{json,session}` (gitignored) | — |

**Missing dependencies with no fallback:** None.
**Missing dependencies with fallback:** Per-account connect requires live Telegram/proxy reachability; unreachable → per-item failure, not batch failure.

## Validation Architecture

*(workflow.nyquist_validation = true in .planning/config.json → section included.)*

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio (`asyncio_mode = "auto"`), SQLAlchemy async |
| Config file | `pyproject.toml` `[tool.pytest.ini_options]`, `testpaths = ["tests"]` |
| Quick run command | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_account_import.py -x` |
| Full suite command | `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest` |

**CRITICAL (CLAUDE.md):** Tests run ONLY via the test-overlay. NEVER `docker compose run --rm api pytest` without the overlay — DATABASE_URL points at prod and the conftest guard (`tests/conftest.py:46-77`) will RuntimeError, but the overlay is the correct path (ephemeral `db-test` in tmpfs, DATABASE_URL → `outreach_test`).

### Phase Requirements → Test Map
| Req | Behavior | Test Type | Automated Command | File Exists? |
|-----|----------|-----------|-------------------|-------------|
| IMPT-03 | `StringSession.save(SQLiteSession(path))` yields a valid, round-trippable string from a real vendor `.session`, no network | unit | `pytest tests/test_account_import.py::test_sqlite_to_stringsession_offline -x` | ❌ Wave 0 |
| IMPT-04 | `make_telegram_client(fingerprint=None)` is byte-identical to today; fingerprint dict overrides device/version/locale but keeps `lang_pack='tdesktop'` | unit | `pytest tests/test_account_import.py::test_fingerprint_override_and_strict_fallback -x` | ❌ Wave 0 |
| IMPT-01 | Preview matches `.json`↔`.session` by basename; reports unpaired + malformed; no connect | unit/integration | `pytest tests/test_account_import.py::test_preview_pairing -x` | ❌ Wave 0 |
| IMPT-05 | 2FA password stored Fernet-encrypted; decrypt round-trips; never in response/log | unit | `pytest tests/test_account_import.py::test_twofa_encrypted_at_rest -x` | ❌ Wave 0 |
| IMPT-06 | Dedup: second import of same tg_id → skip+report "already_connected", existing session untouched; JSON proxy honored else pool | integration | `pytest tests/test_account_import.py::test_dedup_skip_and_proxy -x` | ❌ Wave 0 |
| IMPT-07 | Per-file partial success: one broken pair fails, rest import; imported sender is active/none | integration | `pytest tests/test_account_import.py::test_partial_success_and_start_state -x` | ❌ Wave 0 |
| IMPT-02 | Confirm creates a job + per-item rows, returns job_id; worker drives pending→processing→ok/failed; status endpoint reports progress | integration | `pytest tests/test_account_import_worker.py -x` | ❌ Wave 0 |

Note: the connect + `get_me` calls must be monkeypatched (mirror how `test_kb_ingest_worker.py` patches `embed_texts` and how `test_onboarding.py` stubs Telethon) — no real Telegram network in tests. The offline conversion test (IMPT-03) can use the checked-in vendor sample OR a synthetically-built SQLiteSession fixture (never commit the live `.session`).

### Sampling Rate
- **Per task commit:** `pytest tests/test_account_import.py -x`
- **Per wave merge:** full suite via test-overlay
- **Phase gate:** full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_account_import.py` — covers IMPT-01/03/04/05/06/07 (preview, conversion, fingerprint, 2FA, dedup, partial success)
- [ ] `tests/test_account_import_worker.py` — covers IMPT-02 (async job + status polling), mirrors `test_kb_ingest_worker.py`
- [ ] Telethon connect/get_me stub fixture in `tests/conftest.py` (or per-test) — reuse the onboarding test stubbing approach
- [ ] Vendor SQLiteSession fixture builder (synthetic auth_key) — so the conversion test doesn't depend on the gitignored live sample
- [ ] Framework install: none — pytest infra exists

## Sources

### Primary (HIGH confidence)
- **Running codebase (read directly):** `app/services/telegram.py` (`_CLIENT_FINGERPRINT`:152, `make_telegram_client`:233, `build_proxy_tuple`:170, `get_client`:291), `app/services/encryption.py`, `app/routers/onboarding.py` (`_create_sender_from_session`:295, `_finalize_onboarding_or_reauth`:264), `app/models/__init__.py` (`Sender`:74, `ProxyPool`:479, `CsvImport`:588, `KbDocument`:776), `app/services/kb_ingest_worker.py`, `app/routers/contacts.py` (preview/apply:301), `app/main.py` lifespan, `migrations/049_account_profile.sql`.
- **Empirical verification (executed in api container 2026-07-06):** `SQLiteSession('/tmp/imp.session')` + `StringSession.save(...)` on the real vendor `+18646884306.session` → dc_id=1, 256-byte auth_key, valid `1A…` StringSession, round-trip auth_key match, zero network.
- **Vendor sample (read directly):** `scratchpad/+18646884306.json` (full field set) + `.session` SQLite parse (tables entities/sent_files/sessions/update_state/version; format v7; entities=0).
- **CONTEXT.md / 21-NOTES.md** — locked decisions D-01..D-17 and grounded file analysis.
- **CLAUDE.md** (root + tg-outreach) — migrations idempotent/auto-applied, test-overlay rule, entity-cache cold start, ORM server_default drift, `restart:"no"` on old projects.

### Secondary (MEDIUM confidence)
- Telethon "Session Files" documentation pattern (`StringSession.save(client.session)` conversion idiom) — corroborated by the empirical test above, which promotes it to HIGH for this project.

### Tertiary (LOW confidence)
- None relied upon.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all assets read in-repo; no new deps; Telethon version pinned + conversion executed.
- Architecture: HIGH — every pattern has a verbatim in-repo precedent (onboarding create, KB worker, csv staging).
- Session conversion (D-12): HIGH — executed on the real vendor sample, no network.
- Fingerprint override (D-01/02): HIGH — seam + call sites enumerated; strict-fallback design confirmed against current code.
- Pitfalls: HIGH — sourced from code + documented project memories/incidents.

**Research date:** 2026-07-06
**Valid until:** ~2026-08-06 (stable; re-verify only if Telethon is bumped past 1.42.x or the encryption/onboarding helpers change)
