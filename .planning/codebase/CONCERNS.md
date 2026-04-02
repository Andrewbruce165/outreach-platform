# Codebase Concerns

**Analysis Date:** 2026-04-02

## Tech Debt

**Single global API key — no multi-tenancy auth:**
- Issue: All API access is gated by a single `api_key` in `app/config.py`. One key for all requests. No per-workspace or per-user identity.
- Files: `app/routers/auth.py`, `app/config.py`
- Why: Inherited from the internal AGS Foods single-tenant tool.
- Impact: Can't onboard multiple clients. Any external client would share full access to all senders, all conversations, all AI contexts of every other client. Complete data isolation is impossible with current auth.
- Fix approach: Introduce Workspace model and per-workspace JWT or API key. Every resource (Sender, AIContext, Conversation, MessageQueue) needs a `workspace_id` FK. Add workspace-scoped auth middleware.

**No workspace/tenant isolation in data model — zero rows with workspace_id:**
- Issue: None of the core tables (`senders`, `conversations`, `ai_contexts`, `message_queue`, `messages_log`, `contacts_cache`, `proxy_pool`) have a `workspace_id` column. All data is globally shared.
- Files: `app/models/__init__.py`, `migrations/` (all 11 files — none add workspace_id)
- Why: Original internal tool had one tenant.
- Impact: Every API call exposes all data across all future clients. Filtering by workspace is architecturally impossible without schema migration. This is the largest single blocker for selling to external clients.
- Fix approach: Add `workspace_id UUID NOT NULL FK` to all tenant-scoped tables. Migration must be planned carefully — existing data gets assigned a default workspace. New indexes required on every table. Estimated effort: high.

**Hardcoded Moscow timezone for working hours (two separate places):**
- Issue: Working hours (09:00–20:00 MSK) are hardcoded as constants in two separate files: `queue.py` uses `zoneinfo.ZoneInfo("Europe/Moscow")` with `WORK_HOUR_START = 9 / WORK_HOUR_END = 20`; `warmup.py` uses `MOSCOW_OFFSET = 3` with manual UTC arithmetic. These configs are not configurable per workspace.
- Files: `app/services/queue.py` (lines 61–63), `app/services/warmup.py` (lines 30, 143–145)
- Why: Single-tenant origin, one timezone needed.
- Impact: Future clients in other timezones get messages outside their desired hours. Two divergent implementations are easy to get out of sync when changing hours.
- Fix approach: Move working hours to a Workspace settings table (`tz`, `work_hour_start`, `work_hour_end`). Consolidate timezone logic into a shared utility. Warmup worker should use the same `zoneinfo` approach as queue worker, not manual offset arithmetic.

**Onboarding session state in-process memory:**
- Issue: `_onboarding_sessions` dict in `app/routers/onboarding.py` (line 46) stores mid-flow auth state (TelegramClient instances, phone_code_hash, QR login objects) in Python process memory. Comment says "В продакшене лучше использовать Redis".
- Files: `app/routers/onboarding.py` (line 46)
- Why: Quick implementation, no Redis in current stack.
- Impact: Any API container restart (deploy, crash) silently drops all in-flight onboarding flows. Users get stuck mid-auth with no error. Under multi-tenant load, a user onboarding in one process has no chance of completing if the request lands on a different instance.
- Fix approach: Persist onboarding state (phone_code_hash, session_id, status) in a DB table or Redis. TelegramClient can be re-created from phone on resume if needed.

**Sender role is an unconstrained string field:**
- Issue: `Sender.role` is `Column(String(20), server_default='sender')`. Valid values are `'sender'` and `'checker'` but this is enforced nowhere in the DB or Python layer — only in the `SenderCreate` schema description string.
- Files: `app/models/__init__.py` (line 38), `app/schemas/__init__.py` (line 77)
- Impact: Invalid role values can be inserted silently. Listener and checker services filter on `role = 'sender'` / `role = 'checker'` in raw SQL — a typo in a POST request creates a dead account.
- Fix approach: Convert `role` to a Python `enum.Enum` and `SQLEnum` (same pattern as `QueueItemStatus`). Or add a DB `CHECK` constraint.

**`init_db()` uses SQLAlchemy `create_all` — diverges from manual migrations:**
- Issue: `app/database.py` calls `Base.metadata.create_all` on startup, which creates tables from ORM models. The codebase also has `migrations/` raw SQL files. These two paths can diverge: a migration may add a column that `create_all` doesn't know about, or vice versa.
- Files: `app/database.py` (line 38), `migrations/` directory
- Why: Inherited convenience from early development.
- Impact: On a fresh DB, `create_all` creates the base schema but migrations are never run automatically — there is no migration runner. On an existing DB, `create_all` is a no-op, so it's harmless but misleading.
- Fix approach: Remove `create_all` from `init_db()` or only use it for test environments. Add a startup migration runner (e.g., run numbered SQL files in order) so fresh deploys get the full schema including all migrations.

**Duplicate send resolution logic in `send.py` and `queue.py`:**
- Issue: Sender validation and resolution (check `sender` slug vs. `ai_context_id` rotation) is copy-pasted identically in `POST /api/v1/send` and `POST /api/v1/send-file`.
- Files: `app/routers/send.py` (lines 36–97 and 151–212 — nearly identical blocks)
- Impact: A bug fix or new validation must be applied twice. Already diverged: `/send-file` route has a subtle difference in error handling.
- Fix approach: Extract sender resolution into a shared `_resolve_sender(request, db)` helper function.

**OpenAI model name hardcoded as non-existent model ID:**
- Issue: `ai_engine.py` calls `gpt-5-mini-2025-08-07` as the model name (lines 304 and 376). This model ID does not match any known OpenAI API model. The comment nearby says "Вызываем GPT-4" which contradicts the model string.
- Files: `app/services/ai_engine.py` (lines 304, 376)
- Impact: Every AI response call will fail with an OpenAI `model_not_found` error unless the model ID happens to be valid. Makes the entire AI responder non-functional.
- Fix approach: Replace with the correct model string (e.g., `gpt-4o-mini`) and move the model name to `app/config.py` as a configurable setting.

**Hardcoded DEFAULT_SYSTEM_PROMPT references AGS Foods brand:**
- Issue: The default AI system prompt in `app/services/ai_engine.py` (lines 30–41) hardcodes company name ("AGS Foods"), product domain ("поставщики сельскохозяйственной продукции"), and business rules specific to the original internal tool.
- Files: `app/services/ai_engine.py` (lines 30–41)
- Impact: Any new client that hasn't set a custom `AIContext` will have their AI respond as an AGS Foods agriculture supplier assistant. This is a brand/data leak risk for a SaaS product.
- Fix approach: Replace default prompt with a neutral placeholder or require `system_prompt` to be non-null in `AIContext`. Do not allow the AGS-specific default to reach any tenant's end users.

**`subprocess.run(["docker", "restart", ...])` inside the API process:**
- Issue: `app/routers/senders.py` (lines 36–50) restarts the `telegram-listener` Docker container by shelling out to `docker restart` from within the API process.
- Files: `app/routers/senders.py` (lines 36–50)
- Why: Listener needs to pick up new senders; restarting was the simplest trigger.
- Impact: Requires the Docker socket mounted into the API container (security risk). Fails silently in any non-Docker environment (local dev, tests). Will not scale to multi-container or Kubernetes deploys. The warning on failure is a `logger.warning`, not an error, so callers have no idea the listener didn't restart.
- Fix approach: Use a signal mechanism — e.g., a DB flag, a shared asyncio event, or a lightweight internal HTTP endpoint on the listener container.

## Known Bugs

**AI responder is broken due to invalid OpenAI model ID:**
- Symptoms: Every call to `ai_engine.generate_response()` raises `APIStatusError` (model not found). All AI-generated replies silently return `None`. Conversations receive no AI response.
- Trigger: Any incoming Telegram message that reaches the AI engine.
- Files: `app/services/ai_engine.py` (lines 304, 376)
- Workaround: None — AI is functionally disabled until the model name is corrected.
- Root cause: Model string `gpt-5-mini-2025-08-07` is not a valid OpenAI API model identifier.

**Listener does not pick up new senders added after container start:**
- Symptoms: A sender added via `POST /api/v1/senders` is not listened to until the listener container restarts.
- Trigger: Adding a new sender while the listener is running.
- Files: `app/services/listener.py` (`run()` at line 1086 — calls `get_active_senders()` once at startup only), `app/routers/senders.py` (lines 36–50, restart attempt)
- Workaround: The `_restart_listener()` call in `senders.py` attempts `docker restart` but fails silently without Docker socket access.
- Root cause: Listener loads senders once at boot, no hot-reload mechanism.

**Rate limit counters query `message_queue` table only — missed messages from `messages_log`:**
- Symptoms: Rate limit counting (`_check_rate_limits` in `queue.py`) counts only messages sent via the queue. Messages sent directly (e.g., warmup messages sent by `warmup.py` directly via Telethon) are not counted against the per-sender limits.
- Files: `app/services/queue.py` (lines 225–325), `app/services/warmup.py`
- Impact: Warmup activity does not consume the daily/hourly send budget — potentially allowing a sender to exceed safe Telegram message rates when warmup + queue activity overlap.

**`_get_long_pause_seconds` re-randomises threshold every call — pause logic is statistically broken:**
- Symptoms: The long-pause trigger in `queue.py` generates `pause_every = random.randint(12, 25)` on every call, then checks `recent_count % pause_every == 0`. Because the threshold changes each time, a sender can process many more than 25 consecutive messages without ever triggering a long pause.
- Files: `app/services/queue.py` (lines 154–177)
- Impact: Human-behaviour simulation is weaker than intended; could increase ban risk.
- Workaround: None. The pause does occasionally fire by chance.

## Security Considerations

**CORS wildcard in production:**
- Risk: `allow_origins=["*"]` allows any browser origin to make credentialed requests.
- Files: `app/main.py` (line 59)
- Current mitigation: None — the API key header provides the only auth layer.
- Recommendations: Restrict to Lovable frontend domain(s) in production. Use env-based CORS_ORIGINS setting.

**Callback URLs are not validated or allowlisted:**
- Risk: Any URL can be passed as `callback_url` in send requests. The queue worker POSTs sensitive data (phone numbers, Telegram IDs, message IDs, sender slugs) to arbitrary attacker-controlled URLs.
- Files: `app/services/queue.py` (lines 679–714), `app/schemas/__init__.py` (`callback_url` field with no validation)
- Current mitigation: None.
- Recommendations: Add a per-workspace allowlist of valid callback URLs. At minimum validate that `callback_url` is a valid HTTPS URL.

**Webhook function URLs from AI contexts execute without validation:**
- Risk: `webhook_functions` stored in `AIContext.faq`-adjacent JSONB field can contain arbitrary URLs. The AI engine POSTs conversation data (including phone numbers, contact names, Telegram IDs) to these URLs on every trigger.
- Files: `app/services/ai_engine.py` (lines 198–262)
- Current mitigation: None — any URL stored in `ai_contexts.webhook_functions` is called.
- Recommendations: Validate webhook URLs at `AIContext` creation/update time. Consider per-workspace URL allowlisting.

**Telegram session strings briefly pass through API memory unencrypted:**
- Risk: During onboarding, the `session_string` is held unencrypted in `_onboarding_sessions` dict and in API response bodies (returned to the Lovable frontend before the user POSTs it to `/api/v1/senders`). The session string grants full Telegram account access.
- Files: `app/routers/onboarding.py` (lines 46, 67–70, 80–83)
- Current mitigation: Session is encrypted with Fernet before DB storage (`app/services/encryption.py`).
- Recommendations: Encrypt session string immediately in the onboarding flow before returning it to the client. Do not return raw session strings in API responses.

**No rate limiting on authentication or onboarding endpoints:**
- Risk: `POST /api/v1/onboarding/start` triggers an SMS code send to any phone number. No rate limiting — can be abused to spam SMS to arbitrary numbers.
- Files: `app/routers/onboarding.py`
- Current mitigation: Telegram itself has FloodWait, but this is handled per-session, not globally.
- Recommendations: Add per-IP or per-phone rate limiting on onboarding start endpoint.

**Proxy credentials stored in plaintext JSONB:**
- Risk: `Sender.proxy` column stores SOCKS5 proxy credentials (host, port, username, password) as plaintext JSONB.
- Files: `app/models/__init__.py` (line 39)
- Current mitigation: Database access is restricted.
- Recommendations: Encrypt proxy password field the same way session strings are encrypted.

## Performance Bottlenecks

**`message_queue` table has no TTL/archival — will grow unboundedly:**
- Problem: All sent and failed queue items are retained forever. Every rate-limit check runs `COUNT(*)` and `MAX(finished_at)` queries against the full table for each sender on every queue tick (every 3 seconds).
- Files: `app/services/queue.py` (lines 225–325)
- Measurement: At 150 msgs/day per sender × 10 senders × 365 days = ~547,500 rows after one year. Queries have no index on `(sender_id, status, finished_at)` — confirmed by absence of this composite index in `migrations/010_missing_indexes.sql`.
- Cause: No archival policy, no TTL.
- Improvement path: Add composite index `(sender_id, status, finished_at)`. Archive or delete `status='sent'` rows older than 30 days via a nightly job.

**Queue worker polls all senders every 3 seconds with no backoff:**
- Problem: `_tick()` fires every 3 seconds regardless of queue depth. Each tick queries `DISTINCT sender_id FROM message_queue WHERE status='pending'`, even when the queue is empty.
- Files: `app/services/queue.py` (line 107)
- Measurement: 20 DB queries/minute at idle with zero messages queued.
- Improvement path: Implement exponential backoff when queue is empty (e.g., sleep up to 30s), or use `LISTEN/NOTIFY` PostgreSQL channel triggered on `INSERT INTO message_queue`.

**AI context cache is class-level dict — shared across all requests, no eviction except TTL:**
- Problem: `AIEngine._context_cache` is a class-level dict that grows indefinitely. With many AI contexts it accumulates. TTL is 300s but expired entries are never deleted — they're only overwritten on next access.
- Files: `app/services/ai_engine.py` (lines 47–48, 65–91)
- Measurement: Not currently an issue with single-tenant use, but will grow linearly with workspace count.
- Improvement path: Use `functools.lru_cache` with a maxsize, or `cachetools.TTLCache`.

## Fragile Areas

**`TelegramListener` — single process handles all accounts, no isolation:**
- Files: `app/services/listener.py`
- Why fragile: All sender clients run as asyncio tasks inside one process. An unhandled exception in one client's event handler can propagate and kill the entire listener. `asyncio.gather(*tasks)` on line 1100 will abort all tasks if one raises.
- Common failures: A bad message format causes `TypeNotFoundError`; worked around by `ResilientTelegramClient` but only for `GetDifference` — other unexpected TL types would still crash.
- Safe modification: Wrap each `start_client` task with a top-level `try/except`. Add per-client circuit breakers.
- Test coverage: None — no tests exist in this codebase.

**Listener loads senders once at startup — no dynamic reload:**
- Files: `app/services/listener.py` (line 1090, `run()`)
- Why fragile: New senders added via API are not picked up. The restart-via-Docker hack in `senders.py` works only if Docker socket is mounted.
- Common failures: Senders added after container start are silently ignored.
- Safe modification: Do not add new senders without confirming listener restart.

**Onboarding in-memory sessions are not cleaned up:**
- Files: `app/routers/onboarding.py` (line 46, `_onboarding_sessions` dict)
- Why fragile: Sessions that timeout (user doesn't complete QR flow, etc.) leave `TelegramClient` objects in memory holding open TCP connections to Telegram. `_wait_for_qr` background task (line 149) runs for 120s then marks status but never explicitly disconnects the client on timeout paths.
- Common failures: Memory leak under repeated abandoned onboarding flows. Open connections count against Telegram session limits.
- Safe modification: Always call `client.disconnect()` in every exit path of the onboarding background task.

**`_set_auth_status` is duplicated between `telegram.py` and `listener.py`:**
- Files: `app/services/telegram.py` (line 47), `app/services/listener.py` (line 144)
- Why fragile: Two independent implementations of the same DB write. One updates only `auth_status`; the other also sets `is_active = false`. Behavioural divergence over time is likely.
- Safe modification: Consolidate into a single shared utility function.

**Conversation deduplication relies on `contact_telegram_id` but field is nullable:**
- Files: `app/services/listener.py` (line 362, `get_or_create_conversation`), `app/models/__init__.py` (line 163)
- Why fragile: `Conversation.contact_telegram_id` is nullable. The upsert logic in `listener.py` looks up by `contact_telegram_id`, but if Telegram didn't provide it, every message from the same contact creates a new conversation row.
- Common failures: Contacts with unavailable telegram_id accumulate duplicate conversations.

## Scaling Limits

**Single-container listener — one process, one event loop for all accounts:**
- Current capacity: Works for small numbers of accounts (observed at ~5–10 concurrent senders).
- Limit: CPython GIL + Telethon's asyncio means a spike in incoming messages from one account blocks processing for others. At ~50+ concurrent senders, event loop saturation is expected.
- Symptoms at limit: Incoming messages queued but processed with increasing latency; debounce timers fire late.
- Scaling path: Shard senders across multiple listener instances. Requires the sender-reload mechanism to be solved first.

**PostgreSQL connection pool hardcoded at pool_size=5, max_overflow=10:**
- Current capacity: 15 concurrent DB connections max (5 pool + 10 overflow).
- Files: `app/database.py` (lines 13–16)
- Limit: Three asyncio workers (queue worker, warmup worker, inbound request handler) plus listener container share this pool. Under load, connection exhaustion causes 503s.
- Scaling path: Move `pool_size` and `max_overflow` to `Settings` in `app/config.py` for environment-based tuning.

**`MAX_QUEUE_PER_SENDER = 1000` hardcoded in send router:**
- Files: `app/routers/send.py` (line 253)
- Current capacity: 1000 pending items per sender.
- Limit: Inflexible. A client running a large campaign hits this limit with no way to raise it without code change.
- Scaling path: Move limit to per-workspace or per-sender config.

## Dependencies at Risk

**Telethon — unofficial Telegram MTProto client:**
- Risk: Telethon uses unofficial API access. Telegram has historically banned third-party clients. The `_CLIENT_FINGERPRINT` hack in `telegram.py` spoofs `lang_pack` to mimic Telegram Desktop specifically because the default Telethon fingerprint triggers session termination.
- Files: `app/services/telegram.py` (lines 61–74), `app/services/listener.py`
- Impact: A Telegram policy change or fingerprint update could deactivate all sessions simultaneously.
- Mitigation: The fingerprint spoof currently works. No alternative — the entire product is built on this.

**No `requirements.txt` or `pyproject.toml` with pinned versions visible in repo:**
- Risk: Cannot confirm exact dependency versions in use without inspecting the running container. Version drift on `telethon`, `openai`, or `cryptography` packages could introduce breaking changes.
- Files: Not found in repository root during analysis.
- Impact: Reproducing a bug or rolling back a deploy requires guessing package versions.
- Fix approach: Add a `requirements.txt` or `pyproject.toml` with pinned versions to the repo root.

## Missing Critical Features

**No workspace/multi-tenancy — blocks all external client onboarding:**
- Problem: No `Workspace` model, no workspace-scoped auth. Every resource is globally shared.
- Current workaround: Single internal client only (AGS Foods).
- Blocks: Cannot sell to any external client. Cannot isolate one client's data from another's. Cannot offer per-workspace rate limit configuration or billing.
- Implementation complexity: High — requires DB schema changes across all tables, new auth layer, migration of existing data.

**No personalization variables in message templates:**
- Problem: No `{{name}}`, `{{company}}`, or other variable substitution in message text. The `recipient_name` field is stored but not injected into message body.
- Files: `app/routers/send.py`, `app/services/queue.py`
- Blocks: Personalized outreach campaigns. Every recipient gets the exact same message text.
- Implementation complexity: Low — add variable interpolation in `enqueue_message` using `message_text.format(**variables)` or similar.

**No CSV upload / bulk contact import via UI:**
- Problem: Only API-push (n8n/webhook) contact ingestion exists. No way to upload a CSV of phone numbers and names through the Lovable frontend.
- Blocks: Self-service client campaigns without n8n setup.
- Implementation complexity: Medium — new endpoint `POST /api/v1/contacts/import` accepting CSV, plus Lovable UI.

**No external alerting for PEER_FLOOD / session bans:**
- Problem: `PEER_FLOOD` and account ban events are logged as `CRITICAL` but there is no external alert (email, webhook, Telegram message to admin). The TODO comment in `queue.py` line 480 acknowledges this.
- Files: `app/services/queue.py` (line 480)
- Blocks: Operators don't know an account was banned until they check logs.
- Implementation complexity: Low — add webhook/email call in the PEER_FLOOD and `UserDeactivatedBanError` handlers.

**No auto_pause_triggers enforcement in listener:**
- Problem: `AIContext.auto_pause_triggers` JSONB field exists and is documented, but `listener.py` does not read or evaluate it. The field is stored but never acted upon.
- Files: `app/models/__init__.py` (line 100), `app/services/listener.py`
- Blocks: Automatic AI pause when a contact says stop/unsubscribe/contact sales.
- Implementation complexity: Medium — add keyword match in `handle_incoming_message` against the triggers list.

## Test Coverage Gaps

**Zero test files exist:**
- What's not tested: Everything — queue worker, rate limit logic, AI engine, listener event handlers, onboarding flow, send/batch endpoints, encryption/decryption.
- Risk: Any change to queue timing, rate limits, or Telethon interaction has no safety net. Bugs in encryption mean lost Telegram sessions.
- Priority: High
- Difficulty to test: Telethon and OpenAI require mocking. Queue worker needs async test infrastructure (pytest-asyncio). Telegram onboarding is stateful and hard to integration-test without real accounts.

**Rate limit logic has no unit tests:**
- What's not tested: `_check_rate_limits()` in `queue.py` — the core logic that prevents account bans.
- Risk: A refactor to add workspace-configurable limits could silently break the per-minute/per-hour/per-day caps. Accounts could be banned.
- Priority: High
- Difficulty to test: Requires mocked DB with time-travel (mock `datetime.now()`).

**Encryption/decryption has no tests:**
- What's not tested: `encrypt_session` / `decrypt_session` in `app/services/encryption.py`. Key derivation using SHA-256 + base64 is non-standard and untested.
- Risk: A change to `ENCRYPTION_KEY` env var or key derivation logic silently makes all existing sessions unreadable — all accounts need re-onboarding.
- Priority: High
- Difficulty to test: Simple unit test — no external dependencies needed.

---

*Concerns audit: 2026-04-02*
*Update as issues are fixed or new ones discovered*
