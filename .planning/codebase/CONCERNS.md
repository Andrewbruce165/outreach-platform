# Codebase Concerns

**Analysis Date:** 2026-06-30

---

## CRITICAL — Blocking for External Customers

### No Authentication / Authorization Beyond Single-Workspace Prototype

- Issue: The system authenticates users via Supabase JWT and maps them to a `workspace_id`, but there is **no ownership enforcement at the DB layer** (no Postgres RLS). Every endpoint manually appends `WHERE workspace_id = :wid` to every query. All 20+ `TODO(v2-rls)` comments across the codebase mark locations where these app-level filters will be replaced by RLS policies.
- Files: `app/utils/auth.py:32`, `app/routers/campaigns.py:119,157,175,196,566`, `app/routers/conversations.py:77,413,514,573`, `app/routers/folders.py:93,150,184,231,284`, `app/routers/contacts.py:80,192,386,463,518`, `app/routers/agents.py:123,174,198`, `app/routers/send.py:84`, `app/routers/onboarding.py:146,218,736,777`, `app/routers/workspace.py:115,137,166,203,287,322`, `app/services/rotation.py:67`, `app/services/onboarding_state.py:118`
- Impact: A single code bug (missing `workspace_id` filter in any query) silently exposes another tenant's data. The surface area is large — every SELECT in every router. Current state is safe for a single-tenant deploy but is a hard blocker for multi-tenant external customers.
- Fix approach: Implement Postgres RLS policies on all tenant-scoped tables (`ALTER TABLE … ENABLE ROW LEVEL SECURITY; CREATE POLICY …`) gated on a session variable set at connection time. After RLS is in place, remove the manual `WHERE workspace_id = :wid` clauses and delete the `TODO(v2-rls)` markers.

---

### API Key Revocation Has Up to 5-Minute Cache Lag

- Issue: Workspace API keys are cached in an in-process LRU dict (`_TOKEN_CACHE`) for 5 minutes (`_TOKEN_CACHE_TTL_SECONDS = 300`). A revoked key (`revoked_at` set in DB) continues to authenticate for up to 5 minutes per api container process. The cache is not shared between containers, so a multi-container deploy has independent caches.
- Files: `app/utils/auth.py:61-98`
- Impact: Immediately revoked API keys remain active for up to 5 minutes. Acceptable for v1 single-container, unacceptable for revoke-on-breach scenarios with multi-container scale.
- Fix approach: Add a `revoked_at` check on cache hit (cheap DB lookup or pubsub invalidation), or reduce TTL to ~30s and accept the CPU cost at current scale.

---

### JWT Library (`python-jose`) Is Deprecated

- Issue: `python-jose==3.3.0` is used for JWT verification. The library is unmaintained and has known CVEs. The codebase has a `TODO(v2)` comment to migrate to `PyJWT`.
- Files: `requirements.txt:17`, `app/utils/auth.py:31`
- Impact: Potential security vulnerabilities in JWT parsing. No immediate known exploits, but the library is not patched.
- Fix approach: Replace `from jose import jwt` with `PyJWT` (`pip install PyJWT[crypto]`). The ES256/JWKS path and HS256 fallback both have PyJWT equivalents.

---

## HIGH — Operational Risks

### `docker compose down -v` Wipes Production Data Volume

- Issue: The production PostgreSQL data lives in a named Docker volume (`outreach_platform_db_data`). Running `docker compose down -v` (or `docker compose down` followed by volume pruning) on the production host **permanently deletes the database**. There is no guard preventing this.
- Files: `docker-compose.yml` (volume definition), `/root/apps/aimly/tg-outreach/backup.sh`
- Impact: Total data loss. Recovery requires latest backup from `/root/backups/tg-outreach/` which has 14-day retention. Any data since the last backup (cron runs at 03:05 daily) is lost permanently.
- Fix approach: Daily backups are in place (cron `5 3 * * *`). Document the danger prominently. Consider renaming the volume to something that does not look like a test volume. Add a pre-hook or runbook note to **never** use `-v` on the production host.

---

### Old Projects (`telegram-api`, `outreach-platform`) Still Running Risk

- Issue: `/root/apps/telegram-api/` and `/root/apps/outreach-platform/` are stopped (`restart: "no"` in their docker-compose), but they still exist on disk and share the same 13 Telegram account session files. If either is accidentally restarted (e.g. `docker compose up` in the wrong directory), their listeners will conflict with `tg-outreach` listener, causing double AI replies and session conflicts.
- Files: `/root/apps/telegram-api/docker-compose.yml`, `/root/apps/outreach-platform/docker-compose.yml`
- Impact: Telegram session conflicts → account bans; duplicate AI responses to users. `/root/apps/outreach-platform` still has `restart: unless-stopped` (NOT `restart: no`), making it a live risk.
- Fix approach: Per CLAUDE.md task list — verify sessions migrated, optionally backup DB history, then delete both directories. Until deleted, add a `restart: "no"` override to `/root/apps/outreach-platform/docker-compose.yml` immediately.

---

### ORM `default=` vs `server_default=` Drift — Recurring Pattern

- Issue: SQLAlchemy `default=` (Python-side) does NOT add a `DEFAULT` clause to the physical column when `create_all` is called. If a raw SQL `INSERT` omits that column, it hits `NotNullViolation`. This has caused production incidents twice: `warmup_sessions.status`/`messages_sent` (migration 040) and `kb_chunks.id`, `kb_documents.id`, `knowledge_bases.id` (migration 042). The `sender_restriction_events.id` column was also affected (fixed earlier).
- Files: `app/models/__init__.py` (all models with `default=` instead of `server_default=`), `migrations/040_warmup_sessions_defaults_drift.sql`, `migrations/042_kb_id_server_defaults.sql`
- Impact: Raw SQL inserts in background workers crash with `IntegrityError` on newly-built tables. Pattern will recur with every new table that uses `default=` on a NOT NULL column and has a background worker inserting via raw `text()`.
- Fix approach: Audit every `Column(..., nullable=False, default=X)` in `app/models/__init__.py` and ensure it also has `server_default=X` (or the equivalent cast). New models must use `server_default` on all NOT NULL columns. Failing that, always pass explicit values in raw SQL inserts.

---

### Prod DB Can Be Wiped by Tests If Overlay Is Not Used

- Issue: `tests/conftest.py` contains `DROP SCHEMA public CASCADE; CREATE SCHEMA public;` at line 100 and 262. A guard at lines 40-62 checks DSN markers, but this requires discipline: running `docker compose run --rm api pytest` (without the test overlay) routes to the prod DB and has historically wiped it (2026-05-26 incident, all 22 relations recreated at 13:18:21 UTC with identical `file_mtime`).
- Files: `tests/conftest.py:36-100`, `docker-compose.test.yml`
- Impact: Complete prod data loss. The guard exists but relies on the developer always using the overlay.
- Fix approach: Guard is present and documents the risk. Strictly enforce that all test runs go through `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest`. Consider adding a hostname check as a second guard (e.g. refuse to run if `$HOSTNAME` matches the production container name).

---

### Message Queue Templates Snapshot at Enqueue Time

- Issue: `campaign_enqueue.py` renders `message_template` into `message_queue.message_text` at enqueue time. Editing the campaign template after items are already enqueued does **not** update pending queue rows. The opener is never re-rendered unless a manual re-render call is made (a utility exists in `campaign_enqueue.py:348` but is not exposed as a UI action).
- Files: `app/services/campaign_enqueue.py:14`, `app/routers/campaigns.py:656`
- Impact: Template edits during an active campaign silently have no effect on already-pending sends. No error, no warning. Users believe they changed the message but the old text still goes out.
- Fix approach: Expose a `POST /campaigns/{id}/re-render-pending` endpoint or automatically re-render on PATCH to `message_template`. At minimum, display a UI warning when the template is edited while the campaign has pending queue items.

---

## HIGH — Checker Pool Fragility

### Checker Pool Shadow-Ban / False-Negative Saga

- Issue: Checker accounts that perform bulk `ResolvePhoneRequest` receive a Telegram-side shadow-ban on the contacts API. Two throttle modes exist: soft burst (~45-50 resolves → rare false-negatives, recovers in minutes) and hard shadow-ban (thousands/day → ~0.07% hit-rate, recovers in days). `is_registered=False` conflates four distinct causes: (1) genuinely unregistered, (2) privacy-hidden account, (3) throttled checker, (4) US/cold-account resolver (always returns false for `+79…` numbers). Phase 14 mitigations are deployed but the pool is fragile.
- Files: `app/services/checker.py` (full file), `app/services/contact_check_worker.py`, `app/data/control_set_known_live.txt`, `.planning/notes/checker-false-negatives.md`, `.planning/notes/checker-problem-and-history.md`
- Impact: False-negatives silently discard real leads. The single historic checker `sender-8428118140` reported 2.5% registered vs the true ~26% — projecting ~3,600+ discarded live leads from a 14k contact base. All current checkers are parked as of 2026-06-30 (see memory); no new contacts can be resolved until fresh warmed RU checkers are added.
- Current mitigations: burst cap (30), post-batch rest (`checker_rest_until`), probe-burn escalating backoff (`checker_trip_count`, migration 036), inline throttle detector, restriction-gated selection (Phase 14 RESV-05).
- Remaining risks: (a) Fresh checkers must be RU-registered accounts (`+79…`); US/cold accounts return 100% false-negative. (b) `contacts_cache` is workspace-isolated but **not** sender-isolated — a poisoned checker's false `not_registered` rows persist in the workspace cache even after the checker is retired; rollback to `pending` is partially ineffective because the stale cache row can be served to the next checker on cache hit. Purge false cache rows explicitly when retiring a checker.
- Fix approach: Add a cache-poisoning cleanup path: when a checker transitions to `spam_limited`, DELETE its `contacts_cache` rows where `is_registered=false` within the workspace (matching the manual fix done 2026-06-26). Document the "only warmed RU accounts" constraint prominently in onboarding UI.

---

### `is_registered` Field Name Is Misleading

- Issue: `ContactCache.is_registered` and `Contact.tg_status='not_registered'` mean "not resolvable by this checker via phone" — NOT "definitely no Telegram account". Privacy-hidden accounts score `false`. The field name implies definitiveness that it cannot provide.
- Files: `app/models/__init__.py:197` (`ContactCache.is_registered`), `app/models/__init__.py:489` (`Contact.tg_status`), `app/services/checker.py:15-42`
- Impact: Any analytics, dedup, or "dead number" logic built on `is_registered=false` ≡ "no TG account" will discard real users. Currently no analytics dashboard uses it directly, but the naming will mislead future developers.
- Fix approach: Rename `is_registered` to `phone_resolvable` or add a prominent DB comment. For `tg_status`, rename `not_registered` to `not_resolvable` in a future migration (requires updating all callers).

---

## MEDIUM — Technical Debt

### `MAX_NEW_CONTACTS_PER_HOUR = 15` Still Hardcoded in Queue Worker

- Issue: The per-sender maximum new contacts per hour (`MAX_NEW_CONTACTS_PER_HOUR = 15`) is hardcoded as a module-level constant in `queue.py`. The per-sender send rates (`rate_per_min`, `rate_per_hour`, `rate_per_day`) were moved to DB columns in Phase 2, but this contact-contact limit was not.
- Files: `app/services/queue.py:52`, `app/services/queue.py:634`
- Impact: Cannot configure different contact-rate limits per workspace or per sender without a code change and redeploy. Blocks workspace-level policy configuration needed for v1 external customers.
- Fix approach: Add a `max_new_contacts_per_hour` column to `senders` (or `campaigns`) with a `server_default='15'`. Read it alongside `rate_per_min/hour/day` in `_check_rate_limits`.

---

### `listener.py` Is a 1,773-Line Monolith

- Issue: `app/services/listener.py` is the largest file at 1,773 lines. It handles Telegram event registration, AI response dispatch, conversation upsert, warmup traffic filtering, restriction reconcile sweep, and restriction detection — all in one file.
- Files: `app/services/listener.py`
- Impact: High cognitive load when modifying any one concern. The warmup/internal-filtering logic at lines 610-700 and the restriction reconcile sweep at ~1,036-1,600 are particularly entangled with unrelated message-routing code.
- Fix approach: Extract the restriction reconcile sweep into `app/services/restriction_audit.py` or a new `reconcile.py`. Extract warmup/internal filtering into `app/services/internal_filter.py`.

---

### `ai_engine.py` Is 1,574 Lines

- Issue: `app/services/ai_engine.py` is 1,574 lines handling prompt construction, KB search, tool dispatch, LLM call logging, and response parsing.
- Files: `app/services/ai_engine.py`
- Impact: Changes to prompt templates require navigating the full file. KB search logic is coupled to response generation.
- Fix approach: Extract KB search into `app/services/kb_search.py`, prompt-building into `app/services/prompt_builder.py`.

---

### `queue.py` Has Two Separate Transaction Contexts for FloodWait

- Issue: When a HARD FloodWait or PEER_FLOOD error fires, the worker opens a second `AsyncSessionLocal` context (`db2`) to update `message_queue` / `senders` while the original `db` session is still open. This is done to avoid the main TX holding locks, but creates a pattern where the audit event (written to `db`) and the sender status update (written to `db2`) are in different transactions — a crash between the two leaves inconsistent state. WR-04 mitigates this for the audit event on flood_wait by using the same `db`, but PEER_FLOOD and ACCOUNT_FROZEN still use separate `db2`.
- Files: `app/services/queue.py:930-974` (PEER_FLOOD), `app/services/queue.py:977-1023` (ACCOUNT_FROZEN)
- Impact: If the api process crashes between the two commits, the sender status update (spam_limited/frozen) might be missing while the restriction event row exists — or vice versa. Low probability, but leads to inconsistent audit logs and a sender that can keep sending despite a restriction.
- Fix approach: Combine the sender UPDATE and restriction event INSERT into a single transaction. The `record_restriction_event` helper accepts a `db` parameter — pass the existing `db2` session to it.

---

### Database Connection Pool May Be Undersized

- Issue: The SQLAlchemy async engine is configured with `pool_size=5, max_overflow=10` (15 total connections). The API process runs 6 background workers simultaneously (queue, warmup, onboarding cleanup, contact check, campaign enqueue, KB ingest) plus the FastAPI request handlers. Each worker opens new sessions per tick. Under load, all 15 connections can be exhausted.
- Files: `app/database.py:56-61`
- Impact: `asyncpg.exceptions.TooManyConnectionsError` under moderate load. Not currently observed but may surface as the user base grows or if the KB ingest worker processes large documents.
- Fix approach: Increase `pool_size` to 10-15 and `max_overflow` to 20. Monitor via `pg_stat_activity` in prod.

---

### Supabase JWT `kid` Cache Is Per-Process

- Issue: The JWKS cache (`_JWKS_CACHE`) is a module-level dict, per-process. Each API container warms its own cache. A cache miss triggers one HTTP refetch (handles key rotation), but on a cold start with multiple concurrent requests, all N requests may simultaneously refetch JWKS.
- Files: `app/utils/auth.py:110-170`
- Impact: N simultaneous HTTP calls to Supabase JWKS endpoint on cold start or after key rotation. Minor — Supabase JWKS endpoint is fast and the race is bounded by process startup — but worth noting.
- Fix approach: Add a simple asyncio lock around the JWKS fetch to prevent thundering herd.

---

## MEDIUM — Frontend Drift

### Lovable Frontend Can Drift from OpenAPI Spec

- Issue: The frontend is generated through Lovable from `lovable-handoff/openapi.json`. The frontend sometimes diverges from the spec. Known drift: `POST /conversations/{id}/send` receives `{"message_text": "..."}` instead of the canonical `{"message": "..."}` — worked around via `AliasChoices` in the Pydantic schema. The `GET /telemetry/events` endpoint has a whitelist of 17 events; new events added by Lovable produce 400 errors until the whitelist is updated.
- Files: `app/routers/conversations.py` (`SendMessageFromUIRequest` with `AliasChoices`), `app/routers/telemetry.py` (`_EVENT_WHITELIST`)
- Impact: Frontend changes silently break API calls with no type-level enforcement. Debugging requires correlating API logs with Lovable deployments.
- Fix approach: Keep `lovable-handoff/openapi.json` in sync with the actual FastAPI-generated spec (compare against `/openapi.json` after each phase). Consider adding a CI step to diff the two.

---

### Telethon Entity-Cache Cold Start

- Issue: On first message send via `/conversations/{id}/send`, Telethon may throw `ValueError: Could not find the input entity for PeerUser(user_id=...)` if the peer's `access_hash` is not in the Telethon SQLite session cache. Mitigated by a `get_dialogs(limit=200)` fallback on `ValueError`, but this adds ~500ms latency on first send to any new peer.
- Files: `app/services/telegram.py` (`send_message_by_telegram_id`)
- Impact: Occasional 500ms delay on first send to a peer; gracefully recovered. No data loss.
- Fix approach: Pre-warm entity cache on sender startup by calling `get_dialogs` once. Or maintain `access_hash` in `contacts_cache` and construct `InputPeerUser` directly without a cache lookup.

---

## LOW — Future Issues

### Missing Monitoring / Alerting on Critical Events

- Issue: PEER_FLOOD and ACCOUNT_FROZEN events log at `CRITICAL` level but there is no external alerting (webhook, email, Telegram notification). The queue worker has a comment: `# TODO: add external alert (webhook/email) when monitoring infrastructure is available` (line 955).
- Files: `app/services/queue.py:955`
- Impact: A frozen account is invisible to the operator until they check logs. A spam wave can silence multiple accounts before anyone notices.
- Fix approach: Implement a `notifications.py` service that sends a Telegram message or webhook on `PEER_FLOOD`/`ACCOUNT_FROZEN` events. The `lead_webhook_url` pattern already exists at campaign level — use the same pattern at platform level.

---

### `contacts_cache` Lacks a Unique Constraint on `(workspace_id, phone)`

- Issue: `contacts_cache` has `ON CONFLICT (sender_id, phone) DO UPDATE`. This means workspace-scoped cache reads (`_lookup_cache` filters by `workspace_id`) could theoretically hit rows from a different sender within the same workspace. The workspace-level cache read is correct, but write dedup is keyed to `(sender_id, phone)`, not `(workspace_id, phone)`. Multiple checker rows for the same phone within a workspace are possible.
- Files: `app/services/checker.py:213`, `migrations/020_contacts_cache_unique.sql`
- Impact: Slightly stale cache (the latest-updated row wins due to `ORDER BY updated_at DESC`), but a poisoned checker's `not_registered` row can coexist with a healthy checker's `registered` row for the same `(workspace_id, phone)`. The lookup returns whatever is newest.
- Fix approach: Add a unique constraint on `(workspace_id, phone)` with an `ON CONFLICT DO UPDATE` that only overwrites with `high`-confidence results. Alternatively, query specifically for `tg_confidence='high'` rows on cache lookup.

---

### `CsvImport` Stores Raw File Bytes in PostgreSQL

- Issue: `CsvImport.file_data` is a `LargeBinary` column (i.e. bytea in Postgres) that stores the entire uploaded CSV in the database. Large CSVs (tens of MB) bloat the DB and slow down autovacuum.
- Files: `app/models/__init__.py:543`
- Impact: DB storage growth with each import. The records have an `expires_at` column so expired records should be cleaned up, but there is no background cleanup worker for expired CSV imports.
- Fix approach: Add a scheduled cleanup for expired `csv_imports` rows. Long-term: move file storage to S3/object storage and store only a reference.

---

### No Alerting on Migration Failure at Startup

- Issue: If a migration fails, `init_db()` raises and the API container exits. Docker restarts the container (policy `restart: unless-stopped`), which retries the failing migration infinitely, creating a restart loop. The error is only visible in `docker logs outreach-platform-api`.
- Files: `app/database.py:144-147`, `docker-compose.yml`
- Impact: Silent crash loop. No notification sent. An operator who is not actively watching logs will not know the API is down.
- Fix approach: Add a startup health check endpoint that surfaces migration status. Or send a Telegram message on startup failure (see monitoring concern above).

---

## Operational Runbook Notes

**Safe recovery pattern after prod incident (DROP/fresh DB):**
```bash
docker compose up -d --build api  # applier re-runs all migrations from scratch
```

**Run tests ONLY via:**
```bash
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest
```
Never `docker compose run --rm api pytest` — wipes prod DB.

**Manual backup:**
```bash
/root/apps/aimly/tg-outreach/backup.sh
```

**Check migration state:**
```sql
SELECT version, created_at FROM schema_migrations ORDER BY created_at DESC LIMIT 10;
```

**Checker pool health:**
```sql
SELECT slug, lifecycle_status, restriction_status, restricted_until, checker_rest_until, checker_trip_count
FROM senders WHERE role = 'checker';
```

---

*Concerns audit: 2026-06-30*
