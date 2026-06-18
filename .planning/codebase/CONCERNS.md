# Codebase Concerns

**Analysis Date:** 2026-06-18
**Scope:** Backend (`/root/apps/aimly/tg-outreach`) + Frontend (`/root/apps/aimly/aimly-tg-outreach`)

---

## Backend

### Tech Debt

**App-level workspace isolation instead of Postgres RLS — ~30 scattered TODO markers:**
- Issue: Every router and service filters `WHERE workspace_id = :wid` in application code. Across the entire backend there are ~30 `# TODO(v2-rls): replaced by RLS policy` comments marking code that must be manually updated when RLS lands. A new endpoint that forgets the filter leaks all workspace data silently.
- Files: `app/routers/agents.py`, `app/routers/campaigns.py`, `app/routers/conversations.py`, `app/routers/contacts.py`, `app/routers/folders.py`, `app/routers/onboarding.py`, `app/routers/senders.py`, `app/routers/workspace.py`, `app/routers/analytics.py`, `app/routers/send.py`, `app/services/onboarding_state.py`, `app/services/rotation.py`, `app/utils/auth.py`
- Impact: Isolation is correct today because it is consistently applied, but adding any new resource endpoint risks omitting the filter. Correct fix (Postgres RLS) deferred to v2.
- Fix approach: Implement `SET app.workspace_id` per-request session variable + row-level security policies on all tenant-scoped tables. Replace all application-level `AND workspace_id = :wid` filters.

**`python-jose` is deprecated — JWT library used in production:**
- Issue: `requirements.txt` pins `python-jose[cryptography]==3.3.0`. The library is unmaintained and the codebase itself notes migration to `PyJWT` as `TODO(v2)`. A test (`test_phase5_1_auth_unchanged.py`) actively guards against the migration happening accidentally.
- Files: `app/utils/auth.py` line 46, `requirements.txt` line 17
- Impact: Security vulnerabilities in `python-jose` will not be patched upstream. No CVEs known as of analysis date, but the window grows over time.
- Fix approach: Migrate to `PyJWT` (drop-in for HS256 path; JWKS/ES256 path uses `cryptography` primitives directly). The test guard in `test_phase5_1_auth_unchanged.py` must be updated as part of migration.

**`MAX_NEW_CONTACTS_PER_HOUR = 15` hardcoded in queue worker — not configurable per workspace:**
- Issue: `app/services/queue.py` line 50 hardcodes `MAX_NEW_CONTACTS_PER_HOUR = 15` as a module-level constant. Per-minute/hour/day rates were moved to per-sender DB columns (`rate_per_min/hour/day`), but the unique-contacts-per-hour cap was not.
- Files: `app/services/queue.py` line 50, `_check_rate_limits()` method
- Impact: All workspaces and all senders share the same 15 unique contacts/hour limit. A "safe" workspace with slower campaigns is constrained equally with a high-volume one.
- Fix approach: Add `max_new_contacts_per_hour` column to `senders` table (migration), read from DB in `_check_rate_limits()` like `rate_per_min/hour/day`.

**`spend_usd_cents` always returns 0 — LLM cost tracking is a stub:**
- Issue: `app/routers/analytics.py` line 457 always returns `spend_usd_cents=0`. Per-model pricing is explicitly noted as deferred to v2.
- Files: `app/routers/analytics.py`, `app/schemas/__init__.py` line 921
- Impact: No cost visibility for any workspace. As OpenAI usage grows, operators cannot identify runaway campaigns or bill clients proportionally.
- Fix approach: Store model name + token counts in `llm_calls` (already logged). Add a price lookup table or config dict keyed by model name. Compute spend on query.

**`campaigns.auto_fill` is a stub — returns canned defaults:**
- Issue: `POST /api/v1/campaigns/auto-fill` returns hardcoded default values regardless of the `brief` body parameter sent by the UI. The LLM-driven implementation is deferred.
- Files: `app/routers/campaigns.py` lines 420–460
- Impact: The AI co-pilot button in campaign builder appears functional but produces meaningless output. User trust issue if discovered.
- Fix approach: Implement actual GPT call using `brief` text to generate campaign fields. Gated behind the core-value KPI telemetry proving user demand.

**Webhook callbacks have no HMAC signature — easily spoofed by receivers:**
- Issue: `app/services/webhook_notify.py` line 4 explicitly notes `No HMAC (deferred to v2)`. Both the campaign signal webhooks (`lead/handoff/finish`) and the queue callback (`callback_url`) POST to external URLs without any signature header.
- Files: `app/services/webhook_notify.py`, `app/services/queue.py` `_fire_callback()`
- Impact: Webhook receivers have no way to verify the payload came from this platform. Enables replay attacks and spoofed conversions.
- Fix approach: Add `X-Aimly-Signature: sha256=<hmac>` header using a per-workspace secret. Derive secret from `workspace_api_keys` or add a dedicated `webhook_secret` column to `workspaces`.

**Onboarding state persisted in DB but `TelegramClient` objects still in-process memory:**
- Issue: `app/services/onboarding_state.py` stores session state in the `onboarding_sessions` DB table (migrated from the old in-memory dict), but `TelegramClient` instances (which hold live TCP connections to Telegram) are still held in-process via `_clients` dict.
- Files: `app/services/onboarding_state.py` lines 1–50
- Impact: Container restart during onboarding loses the live client. Multi-container deploys (load balancer) will fail mid-flow if the next request hits a different instance that has no client object for the session.
- Fix approach: Reconstruct client from persisted `phone`/session state on each request within the same pod. Document that onboarding flows require session affinity if running multiple API instances.

**`listener.py` creates its own SQLAlchemy engine — duplicate connection pool:**
- Issue: `app/services/listener.py` lines 65–70 create a separate `create_async_engine` and `async_sessionmaker` rather than importing from `app/database.py`. The listener container has its own pool of up to 5 connections independent of the API pool.
- Files: `app/services/listener.py` lines 65–70
- Impact: Two independent connection pools to the same Postgres. On a constrained VPS this doubles connection overhead. Cannot centrally tune pool settings via `settings.max_pool_size`.
- Fix approach: Export a reusable engine factory from `app/database.py` that both the API and listener import.

**`listener.py` logging hardcoded to `DEBUG` level:**
- Issue: `app/services/listener.py` line 59 calls `logging.basicConfig(level=logging.DEBUG, ...)` unconditionally. This ignores the `settings.log_level` setting and emits verbose SQL + Telethon internals in production.
- Files: `app/services/listener.py` line 59
- Impact: Listener log volume is uncontrolled. Sensitive data (message text, phone numbers) appears in DEBUG-level traces that may be shipped to log aggregators.
- Fix approach: Replace hardcoded `DEBUG` with `getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)` matching the API pattern.

**`_get_long_pause_seconds` re-randomises threshold on every call — statistically broken:**
- Issue: `app/services/queue.py` `_get_long_pause_seconds()` calls `random.randint(LONG_PAUSE_EVERY_MIN, LONG_PAUSE_EVERY_MAX)` on every invocation and then checks `recent_count % pause_every == 0`. Because `pause_every` changes each time, the modulo condition rarely holds; long pauses fire far less often than the human-behaviour simulation intends.
- Files: `app/services/queue.py` lines 246–269
- Impact: Anti-ban human behaviour simulation weaker than designed. Increased long-term ban risk at scale.
- Fix approach: Persist `next_long_pause_at` timestamp per sender (in `senders` table or a side table). Compute once at the end of each long pause, not on every tick.

**`message_queue` table grows unboundedly — no archival:**
- Issue: Sent and failed items are never deleted. Rate-limit queries count `WHERE sender_id = :sid AND status = 'sent' AND finished_at >= :since` against the full table on every tick.
- Files: `app/services/queue.py` `_check_rate_limits()`
- Impact: After 6–12 months at scale, rate-limit queries on unindexed full scans degrade performance noticeably.
- Fix approach: Add a nightly archival job that moves `status IN ('sent','failed') AND finished_at < NOW() - INTERVAL '30 days'` rows to a separate `message_queue_archive` table. Existing composite index in `migrations/010_missing_indexes.sql` helps but does not solve growth.

### Known Bugs

**`PEER_FLOOD` event fires no external alert — silent account suspension:**
- Symptoms: When a sender receives `PEER_FLOOD`, the queue marks tasks paused 24h and logs `CRITICAL` only. There is no webhook, email, or Telegram notification to operators.
- Files: `app/services/queue.py` lines 688–713, line 701 has `# TODO: add external alert`
- Trigger: Telegram spam score threshold exceeded on any sender.
- Workaround: Monitor `docker logs` manually for CRITICAL entries.

**`agent_deleted`, `agent_duplicated`, `agent_updated`, `campaign_stopped` telemetry events return 400:**
- Symptoms: Frontend fires these four event names via `track()` calls; the backend `_EVENT_WHITELIST` does not include them. The `sendBeacon` call silently returns 400 (swallowed client-side), but the events are lost and produce a schema-drift warning in logs.
- Files: `app/routers/telemetry.py` `_EVENT_WHITELIST`, `app/schemas/__init__.py`
- Trigger: User deletes/duplicates an agent, or stops a campaign in the UI.
- Workaround: None — telemetry data for these actions is permanently missing.
- Fix approach: Add the four missing events to `_EVENT_WHITELIST` in `app/routers/telemetry.py`.

**`allow_recontact` / `recontact_min_age_days` campaign fields not exposed in API schema — frontend cannot set them:**
- Symptoms: `campaigns.allow_recontact` column exists (migration 026) and is read by `campaign_enqueue.py` and `queue.py`, but is absent from `CampaignCreate` / `CampaignUpdate` Pydantic schemas. No frontend control exists. The field defaults to `false`.
- Files: `app/schemas/__init__.py` `CampaignCreate`, `CampaignUpdate`; `app/models/__init__.py` line 506
- Impact: Re-contact policy cannot be changed from the UI or API. Documented feature is silently inaccessible.
- Fix approach: Add `allow_recontact: bool = False` and `recontact_min_age_days: int = 30` to `CampaignCreate`/`CampaignUpdate` schemas and expose in the router PATCH handler.

### Security Considerations

**`.env` file committed to frontend git history (Supabase anon key exposed):**
- Risk: `/root/apps/aimly/aimly-tg-outreach/.env` is tracked in git (confirmed via `git ls-files`). It contains `VITE_SUPABASE_ANON_KEY` and `VITE_SUPABASE_URL`. The anon key is a publishable key by Supabase design, but committing the file sets a dangerous precedent and leaks the Supabase project URL to anyone with repo access.
- Files: `/root/apps/aimly/aimly-tg-outreach/.env`
- Current mitigation: Anon key is low-risk (public-facing), but `.env` should never be committed.
- Recommendations: Add `.env` to `.gitignore` immediately (currently only `*.local` is ignored). Rotate the anon key in Supabase dashboard to be safe. Use Lovable project Settings → Environment Variables for build-time secrets instead.

**Callback URLs for queue items are not validated — SSRF risk:**
- Risk: `callback_url` in `POST /api/v1/send` body is stored as-is and the queue worker POSTs sensitive data (phone, Telegram ID, sender slug) to it. An attacker who can enqueue a message (via a compromised `wsk_` key) can exfiltrate data to any URL including internal network addresses.
- Files: `app/schemas/__init__.py` `SendRequest.callback_url`, `app/services/queue.py` `_fire_callback()`
- Current mitigation: None. Any HTTPS or HTTP URL is accepted.
- Recommendations: Validate callback URLs are HTTPS. Add per-workspace callback URL allowlist. Block RFC-1918 addresses (SSRF mitigation).

**Per-workspace API key revocation is not immediate — 5-minute cache lag:**
- Risk: After `DELETE /api/v1/workspace/api-keys/{key_id}`, the revoked key continues to authenticate for up to 5 minutes while it lives in the in-process token cache.
- Files: `app/utils/auth.py` `_TOKEN_CACHE`, `_TOKEN_CACHE_TTL_SECONDS = 300.0`
- Current mitigation: Accepted risk documented in code comments. Multi-container: each container has its own cache, so revocation lag varies.
- Recommendations: For v2 multi-container: use Redis pubsub for immediate revocation propagation. For v1: reduce TTL to 60s.

**Telegram session strings stored encrypted but proxy credentials stored in plaintext JSONB:**
- Risk: `senders.proxy` stores SOCKS5 credentials (host, port, username, password) as unencrypted JSONB. DB dump leaks proxy passwords.
- Files: `app/models/__init__.py` `Sender.proxy`
- Current mitigation: Telegram sessions are encrypted (`app/services/encryption.py`). Proxy passwords are not.
- Recommendations: Encrypt `proxy.password` using the same Fernet scheme before storing.

**No rate limiting on onboarding endpoints — SMS spam vector:**
- Risk: `POST /api/v1/onboarding/start` accepts any phone number and triggers an SMS code send. No per-IP or per-phone rate limiting at the application layer.
- Files: `app/routers/onboarding.py`
- Current mitigation: Telegram FloodWait fires per-session. No application-layer defence.
- Recommendations: Add FastAPI rate-limit middleware (e.g., `slowapi`) on onboarding start endpoint. Apply per-IP and per-phone limits.

### Performance Bottlenecks

**Queue tick: 4 separate `COUNT(*)` queries per sender per tick — no batching:**
- Problem: `_check_rate_limits()` executes 4 sequential SQL queries (msgs last minute, msgs last hour, unique contacts last hour, msgs last 24h) for every eligible sender on every 3-second tick. At 10 senders this is 40 queries every 3 seconds.
- Files: `app/services/queue.py` lines 399–491
- Cause: Each count uses a separate DB round-trip.
- Improvement path: Combine into a single CTE query returning all four counts in one round-trip.

**Inbox polling at 10-second interval — high read amplification:**
- Problem: Frontend `src/routes/_authenticated/inbox.tsx` uses `refetchInterval: 10_000` for conversation list and `refetchInterval: 15_000` for message threads. No WebSocket or SSE channel exists. Each polling call hits `GET /api/v1/conversations` which runs a JOIN + COUNT query.
- Files: `/root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/inbox.tsx` lines 83, 625, 634
- Cause: No push mechanism; polling is the only option.
- Improvement path: Add Postgres `LISTEN/NOTIFY` + SSE endpoint on the API for inbox updates. Reduce polling interval as interim measure only if DB is under pressure.

### Fragile Areas

**`TelegramListener` — one exception kills all accounts:**
- Files: `app/services/listener.py`
- Why fragile: All sender clients run as asyncio tasks in a single process. An unhandled error in one client's event handler propagates through `asyncio.gather`. `ResilientTelegramClient` only wraps `GetDifference` — other unexpected TL types still propagate.
- Safe modification: Wrap each `start_client` task with a per-client `try/except Exception` loop that logs and restarts only the affected client. Do not let any single-client error reach the gather level.
- Test coverage: Tests exist for listener reconcile (`test_listener_reconcile.py`) but not for error propagation paths.

**`_apply_migrations` is fail-fast — a bad migration blocks all API starts:**
- Files: `app/database.py` `_apply_migrations()`
- Why fragile: If any migration file fails, the API container exits immediately and Docker Compose restart loops. A syntax error in a new migration file can take the service down until the file is fixed and the container is rebuilt.
- Safe modification: Always test migration files in the test overlay (`docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/test_migration_*.py`) before deploying. Never deploy migration files without a corresponding `test_migration_NNN.py` file.

**`create_all` + raw migrations coexist — schema drift risk:**
- Files: `app/database.py` `init_db()`, `migrations/` directory
- Why fragile: `Base.metadata.create_all` runs on every startup creating ORM-declared tables. The raw SQL migration applier then runs. If an ORM model adds a column but no corresponding migration file is written, the column exists on fresh DBs (from `create_all`) but not on existing prod DB (migration not applied). The 2026-05-26 incident was caused by the reverse.
- Safe modification: Every ORM model change must have a corresponding `NNN_*.sql` migration. Use `019_schema_drift_fix.sql` as the precedent pattern.

---

## Frontend

### Tech Debt

**Lovable-generated commit history has no structured change log:**
- Issue: All recent frontend commits are authored by `gpt-engineer-app[bot]` with messages like "Changes" or "Добавил кнопку и статус в кампанию". No semantic commit convention.
- Files: `/root/apps/aimly/aimly-tg-outreach` git log
- Impact: Cannot audit what changed in a given deploy. Regressions are hard to bisect.
- Fix approach: Establish human-authored commit convention for feature-level changes. Lovable UI changes can remain bot-authored but should tag the Lovable edit ID in the message (already partially done).

**`telemetry.ts` has duplicate entries in `TelemetryEvent` union type:**
- Issue: `campaign_launched`, `campaign_paused`, `campaign_resumed` appear twice in the `TelemetryEvent` type definition.
- Files: `/root/apps/aimly/aimly-tg-outreach/src/lib/telemetry.ts` lines 20–26
- Impact: TypeScript does not error on duplicate union members. The duplication is cosmetic but indicates the file was edited by Lovable without deduplication and masks the total event count.
- Fix approach: Deduplicate the union type. Run `tsc --noEmit` in CI.

**`inbox.tsx` is 1324 lines — God component:**
- Issue: The entire inbox UI (conversation list, message thread, AI toggle, LLM trace panel, send form) lives in a single route file.
- Files: `/root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/inbox.tsx`
- Impact: Lovable re-generates this file on every UI edit, causing large diffs and high merge conflict risk. Logic is hard to test in isolation.
- Fix approach: Extract `ConversationList`, `MessageThread`, `SendForm`, and `LLMTracePanel` into separate component files under `src/components/inbox/`.

**No TypeScript strict mode or `noUncheckedIndexedAccess`:**
- Issue: `tsconfig.json` (not analysed — no file found in frontend root) likely uses Lovable defaults. No evidence of `strict: true` enforced.
- Files: `/root/apps/aimly/aimly-tg-outreach/` (root tsconfig not located during analysis)
- Impact: Runtime `undefined` errors that TypeScript would catch under strict mode reach users.
- Fix approach: Enable `"strict": true` in tsconfig. Fix resulting type errors before enabling in CI.

### Security Considerations

**`.env` committed to git — Supabase project URL and anon key exposed:**
- Risk: See Backend § Security above. Same file — the frontend repo has `.env` tracked. `VITE_SUPABASE_URL` reveals the Supabase project identifier. `VITE_SUPABASE_ANON_KEY` is a publishable key but its exposure in git history is a security anti-pattern.
- Files: `/root/apps/aimly/aimly-tg-outreach/.env` (confirmed tracked: `git ls-files` returns `.env`)
- Current mitigation: `.gitignore` only ignores `*.local` — `.env` is not ignored.
- Recommendations: `git rm --cached .env`, add `.env` to `.gitignore`, rotate the key, use Lovable environment variable settings instead.

**`VITE_BACKEND_URL` baked into build — requires rebuild to change backend URL:**
- Issue: `src/lib/api.ts` line 4 reads `import.meta.env.VITE_BACKEND_URL` at build time. The value `https://aimly.agsventurelab.com` is baked into the Cloudflare bundle. Changing the backend URL requires a new Lovable build and Cloudflare deploy.
- Files: `/root/apps/aimly/aimly-tg-outreach/src/lib/api.ts`, `/root/apps/aimly/aimly-tg-outreach/src/lib/telemetry.ts`
- Impact: No runtime override possible. Any backend migration (domain change, staging environment) requires a full frontend rebuild.
- Fix approach: For staging, use Cloudflare Pages environment variables. For runtime config, expose a `/api/v1/config` public endpoint returning backend metadata (not practical for Vite; document the rebuild requirement instead).

**Telemetry events sent unauthenticated on `pagehide` (sendBeacon path):**
- Issue: `src/lib/telemetry.ts` `flush(beacon=true)` uses `navigator.sendBeacon` which cannot set `Authorization` headers. Telemetry events fired on page close are sent without a JWT. The backend `auth_dep` requires auth on `POST /api/v1/telemetry/events`.
- Files: `/root/apps/aimly/aimly-tg-outreach/src/lib/telemetry.ts` lines 57–68
- Impact: `pagehide` events silently fail with 401. Signup/session-end events are lost.
- Fix approach: Backend telemetry endpoint should accept an anonymous path for events fired on page unload, or frontend should include the token as a query parameter for the beacon URL (non-ideal but standard workaround).

### Performance Bottlenecks

**Dashboard polls 5 separate endpoints every 30 seconds — fan-out on every refetch:**
- Problem: `src/routes/_authenticated/index.tsx` has 5 separate `useQuery` hooks each with `refetchInterval: 30_000`. Every 30 seconds the dashboard fires 5 concurrent API calls.
- Files: `/root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/index.tsx` lines 55–89
- Cause: No aggregated dashboard endpoint; each card fetches independently.
- Improvement path: Add `GET /api/v1/analytics/dashboard` that returns all five counts in a single query. The backend `AnalyticsCards` schema already covers most fields.

---

## Frontend ↔ Backend Contract Drift

**4 telemetry events fired by frontend not whitelisted in backend — silent data loss:**
- Issue: Frontend `TelemetryEvent` type and `track()` calls reference `agent_deleted`, `agent_duplicated`, `agent_updated`, `campaign_stopped`. Backend `_EVENT_WHITELIST` in `app/routers/telemetry.py` does not include these four names.
- Frontend fires: `src/routes/_authenticated/agents.tsx` lines 57, 66, 262 (`agent_deleted`, `agent_duplicated`, `agent_updated`). `campaign_stopped` is in the type definition but no active `track()` call was found for it.
- Backend result: 400 `UNKNOWN_EVENT`. `sendBeacon` path swallows silently; regular fetch path also swallows (`.catch(() => undefined)` in telemetry).
- Impact: Telemetry for agent CRUD actions is permanently missing from `telemetry_events` table.
- Fix: Add the three active events (`agent_deleted`, `agent_duplicated`, `agent_updated`) to `_EVENT_WHITELIST` in `app/routers/telemetry.py`. Decide whether `campaign_stopped` should be added as well. This is a 3-line fix.

**`message` vs `message_text` field name in `/conversations/{id}/send`:**
- Issue: OpenAPI spec defines `message` as the field name for `SendMessageFromUIRequest`. Lovable's generated client sends `message_text`. Backend works around this via `AliasChoices("message", "message_text")` in `app/schemas/__init__.py` line 770.
- Files: `app/schemas/__init__.py`, `/root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/inbox.tsx` (sends the field), `docs/openapi.json`
- Impact: The mismatch is patched and functional, but if the frontend ever regenerates its OpenAPI client from `docs/openapi.json` it will send `message` (canonical), while any handwritten frontend code may send `message_text`. Two code paths for the same field increase fragility.
- Fix: Update `docs/openapi.json` to document `message_text` as an accepted alias, or regenerate the frontend types from the live backend schema.

**`allow_recontact` / `recontact_min_age_days` backend fields not in frontend OpenAPI spec:**
- Issue: Backend `campaigns` table has `allow_recontact` (migration 026, committed 2026-06-18). This column is read by the campaign enqueue worker and queue worker. Neither `docs/openapi.json` nor `src/types/api.ts` exposes these fields. The frontend `CampaignResponse` / `CampaignCreate` / `CampaignUpdate` types have no knowledge of re-contact policy.
- Files: Backend: `app/models/__init__.py` line 506, `migrations/026_campaign_allow_recontact.sql`. Frontend: `/root/apps/aimly/aimly-tg-outreach/docs/openapi.json`, `src/types/api.ts` (generated).
- Impact: UI users cannot configure re-contact policy even though the backend honours it. Feature exists but is inaccessible.
- Fix: Add `allow_recontact` and `recontact_min_age_days` to `CampaignCreate`/`CampaignUpdate`/`CampaignResponse` Pydantic schemas, regenerate `openapi.json`, rebuild frontend types.

**Frontend `docs/openapi.json` is a static snapshot — diverges from live backend schema:**
- Issue: `/root/apps/aimly/aimly-tg-outreach/docs/openapi.json` is a hand-updated snapshot used to generate `src/types/api.ts`. The backend's actual OpenAPI schema at `/openapi.json` is authoritative. Any backend schema change that is not reflected in `docs/openapi.json` creates silent type drift.
- Files: `/root/apps/aimly/aimly-tg-outreach/docs/openapi.json`, `/root/apps/aimly/aimly-tg-outreach/src/types/api.ts`
- Impact: Frontend TypeScript types may accept/send fields that the backend rejects, or miss new fields the backend returns. Known current gap: `allow_recontact` (see above).
- Fix: Add a CI step (or pre-commit hook) that fetches `/openapi.json` from staging backend and diffs against `docs/openapi.json`. Alert on divergence. Automate type generation from the fetched spec.

**Inbox `send` endpoint: frontend sends `message_text`, OpenAPI type `SendMessageFromUIRequest` exposes `message`:**
- Issue: `src/types/api.ts` `SendMessageFromUIRequest` (auto-generated) defines field `message` (canonical). Actual fetch calls in inbox use the `message_text` alias. If a future Lovable regeneration of fetch calls uses the typed interface, it will switch to `message` — which also works due to `AliasChoices` — but the inconsistency is a maintenance hazard.
- Files: `/root/apps/aimly/aimly-tg-outreach/src/routes/_authenticated/inbox.tsx`, `src/types/api.ts`
- Impact: Currently functional. Risk is future breakage if alias logic is ever removed from the backend.
- Fix: Standardise on one field name. Recommend `message_text` since it is what Lovable naturally generates; update the backend schema to make it canonical (currently it's the alias).

---

## Cross-Cutting Concerns

**No external alerting infrastructure — PEER_FLOOD and bans are invisible:**
- Issue: Critical Telegram events (PEER_FLOOD, `UserDeactivatedBanError`, `AuthKeyError`) log at `CRITICAL` level but fire no webhook or notification. The `# TODO: add external alert` comment in `queue.py` line 701 acknowledges this.
- Files: `app/services/queue.py` lines 688–713, `app/services/listener.py` lines 1221
- Impact: Operators only discover account bans when checking logs manually. For a SaaS product with external clients, this is unacceptable uptime posture.
- Fix: Add a `notify_critical(event, details)` utility that POSTs to a configurable `ALERT_WEBHOOK_URL` env var (or sends a Telegram message to an admin chat). Wire into PEER_FLOOD and ban handlers.

**No monitoring / health dashboards for the outreach platform:**
- Issue: The parent AGS Foods infrastructure has Grafana, but there are no dashboards tracking queue depth, send success rate, FloodWait frequency, or listener uptime for this application.
- Files: `/root/apps/aimly/tg-outreach/` — no Grafana provisioning, no metrics endpoint beyond `/api/v1/health`
- Impact: Silent degradation. A stuck queue or banned sender goes unnoticed until a user reports missed messages.
- Fix: Add a `GET /api/v1/metrics` endpoint returning queue depth, senders by status, recent FloodWait count. Connect to Grafana datasource.

---

*Concerns audit: 2026-06-18*
