# External Integrations

**Analysis Date:** 2026-06-30

---

## Telegram MTProto (Telethon)

**Purpose:** Core platform function — sending outreach messages, listening for replies, onboarding accounts, checking phone registration.

**SDK:** `telethon==1.42.0` via `requirements.txt`

**Auth:** Telegram app credentials loaded from env: `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` (from my.telegram.org). Individual account sessions stored as encrypted `StringSession` strings in `senders.session_string` column (Fernet-encrypted, `app/services/encryption.py`).

**Key TL methods used:**
- `ImportContactsRequest` / `ResolvePhoneRequest` — phone registration check (`app/services/checker.py`)
- `ResolveUsernameRequest` — @username resolution for privacy-hidden accounts (`app/services/checker.py::check_usernames`)
- `GetDialogsRequest` (via `get_dialogs(limit=200)`) — entity-cache warm-up on cold start (`app/services/telegram.py::TelegramService.send_message_by_telegram_id`)
- `GetDifferenceRequest` / `GetChannelDifferenceRequest` — update polling (wrapped in `ResilientTelegramClient`, `app/services/listener.py`)

**Proxy support:** SOCKS5/SOCKS4/HTTP proxy tuples via `PySocks`, built by `build_proxy_tuple()` in `app/services/telegram.py`. Per-sender proxy from `senders.proxy JSONB` column.

**Error handling:** `FloodWaitError`, `PeerFloodError`, `UserDeactivatedBanError`, `FROZEN_*` RPC errors, `AuthKey*Error` family — all handled in `app/services/telegram.py` and `app/services/queue.py`. SpamBot date parsing via `parse_spambot_limit_until()` for restriction detection.

**Session management:** Telethon `StringSession` — serialized to string, encrypted at rest. Cold-start entity-cache issue: if `access_hash` not cached, `get_dialogs(limit=200)` is called as fallback (~500ms).

**Warmup inter-account chats:** `app/services/warmup.py` — sends AI-generated messages between workspace's own accounts on topics from `WARMUP_TOPICS`. Works 09:00–20:00 MSK, 5 warmup levels (days 0–3, 3–7, 7–14, 14–21, 21+).

---

## OpenAI

**Purpose:** AI response generation for incoming Telegram messages; knowledge base text embedding.

**SDK:** `openai>=1.40.0,<2.0.0` — `AsyncOpenAI` (async client)

**Client instantiation:** Module-level singleton at `app/services/ai_engine.py:39` — `client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))`. The same client instance is reused by `app/services/kb_ingest.py` (imported directly).

**Chat completions:**
- Env: `OPENAI_MODEL` (default `gpt-5-mini-2025-08-07`)
- Used by: `app/services/ai_engine.py::generate_response` — generates AI replies in conversations
- Also used by: `app/services/warmup.py` — warmup dialog generation
- Function calling: 3 built-in tools (`mark_as_lead`, `transfer_to_manager`, `finish_conversation`) + optional campaign-level custom tools from `campaigns.tools JSONB`

**Embeddings:**
- Env: `OPENAI_EMBEDDING_MODEL` (default `text-embedding-3-small`, 1536 dims)
- Used by: `app/services/kb_ingest.py::embed_texts` — batch embeds KB document chunks (batch size ≤256)
- Used by: `app/services/kb_search.py::embed_query` — single query embedding for RAG search

**LLM audit logging:** Every `generate_response` call is logged to `llm_calls` table via `app/services/llm_logger.py`. Never raises (Pitfall 5 — log failure must not kill AI response path). Full prompt stored in `llm_calls.prompt JSONB`.

**Error types handled:** `APIError`, `APIConnectionError`, `RateLimitError`, `APIStatusError` (from `openai` package).

---

## Supabase

**Purpose:** Authentication for the frontend — user identity, JWT issuance.

**Frontend SDK:** `@supabase/supabase-js` 2.106.1 — `src/lib/supabase.ts`
- Auth flow: OTP email (magic link), PKCE flow
- Session persistence: `localStorage` (browser-only, SSR stub in place)
- Frontend env: `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`

**Backend JWT verification:** `app/utils/auth.py`
- Primary path: ES256 (asymmetric EC P-256) via JWKS endpoint `${SUPABASE_URL}/auth/v1/.well-known/jwks.json` — Supabase default since Oct 2025
- Fallback path: HS256 via `SUPABASE_JWT_SECRET` env var (legacy)
- JWKS cache: in-process per-container, 1h TTL, `kid`-miss triggers one refetch
- JWT library: `python-jose[cryptography]`

**Dual auth:** `app/utils/auth.py::auth_dep` — accepts either `Authorization: Bearer <JWT>` (Supabase) or `X-Workspace-Key: wsk_<token>` (workspace API key for n8n/integrations). Both resolve to `AuthCtx(workspace_id, user_id, source, role)`.

**Workspace API key:** bcrypt-hashed in `workspace_api_keys.bcrypt_hash`; in-process token cache (5-min TTL, max 1024 entries) to avoid bcrypt overhead on n8n push at scale (`app/utils/auth.py::_TOKEN_CACHE`).

**Lazy workspace creation:** valid JWT + no `user_workspaces` row → atomic create in single transaction (TENT-02, `app/routers/workspace.py::POST /api/v1/auth/me`).

---

## pgvector (PostgreSQL Extension)

**Purpose:** Cosine-distance vector search for RAG knowledge base retrieval.

**Image:** `pgvector/pgvector:pg16` (docker-compose.yml) — PostgreSQL 16 with `vector` extension pre-compiled.

**Python package:** `pgvector==0.4.2` — SQLAlchemy column type `Vector(1536)` used in `KbChunk.embedding` (`app/models/__init__.py:759`).

**Extension init:** `CREATE EXTENSION IF NOT EXISTS vector` called in `app/database.py::init_db` BEFORE `Base.metadata.create_all` — required ordering (pitfall: fresh DB crashes if extension missing when create_all emits `VECTOR(1536)` column).

**Search query:** `<=>` operator (cosine DISTANCE — lower = closer). `ORDER BY embedding <=> :qvec ASC` with `distance <= max_distance` threshold (default 0.8). See `app/services/kb_search.py`.

**Workspace isolation:** KB search WHERE filters BOTH `workspace_id` AND `kb_id IN (...)` — defence-in-depth prevents cross-workspace data leaks.

---

## Decodo Proxy Pool (ISP Static Residential Proxies)

**Purpose:** Per-sender static residential proxies to reduce Telegram spam detection risk.

**Provider:** Decodo (not an SDK — raw SOCKS5 proxies)

**Config:** `DECODO_HOST`, `DECODO_USERNAME`, `DECODO_PASSWORD`, `DECODO_PORTS` env vars (comma-separated port list)

**Management:** `app/routers/proxy_pool.py` — `POST /api/v1/proxy-pool/init` populates pool from env vars; `GET /api/v1/proxy-pool` shows status. Each pool entry is a `proxy_pool` DB row with `assigned_to_sender_id`.

**Usage:** `senders.proxy JSONB` column stores assigned proxy dict `{"type": "socks5", "host": "...", "port": N, "username": "...", "password": "..."}`. Applied in `app/services/telegram.py::make_telegram_client`.

**Protocol:** SOCKS5 (primary), SOCKS4 and HTTP also supported via `_PROXY_TYPE_MAP` in `app/services/telegram.py`.

---

## n8n Webhook Integration

**Purpose:** Outbound webhook notifications to n8n (or any HTTP endpoint) on conversation signals; inbound contact push via workspace API key.

**Outbound webhooks (campaign signals):** `app/services/webhook_notify.py::notify_signal`
- Events: `lead`, `handoff`, `finish`
- Fired fire-and-forget (`asyncio.create_task`) — never blocks AI response path
- Payload: event_type, campaign_id, conversation_id, workspace_id, contact (phone/telegram_id/name/username/custom), reason, last-20-messages history excerpt
- URL resolution: `campaign.{event_type}_webhook_url` → fallback to `campaign.webhook_url`
- Timeout: 30 seconds

**Custom tool webhooks:** AI Function Calling can trigger campaign-defined custom tools (from `campaigns.tools JSONB`), each with a `webhook_url`. Dispatched by `app/services/ai_engine.py`.

**Document webhooks:** listener can forward received documents to `app/services/listener.py::send_document_to_webhook`.

**Inbound contact push:** POST `api/v1/send` or campaign contacts endpoint with `X-Workspace-Key` header — n8n can push contacts programmatically.

---

## Cloudflare Workers (Frontend Deploy)

**Purpose:** Edge hosting for the React frontend.

**Config:** `/root/apps/aimly/aimly-tg-outreach/wrangler.jsonc`
- `name: "tanstack-start-app"`
- `compatibility_date: "2025-09-24"`
- `compatibility_flags: ["nodejs_compat"]`
- `main: "src/server.ts"` — SSR entry point

**Build plugin:** `@cloudflare/vite-plugin` in package.json (included via `@lovable.dev/vite-tanstack-config`)

**Server entry:** `/root/apps/aimly/aimly-tg-outreach/src/server.ts` — wraps TanStack Start's SSR runtime with Cloudflare error handling.

**Git repo:** `AGS-Venture-Lab/aimly-tg-outreach` (GitHub). Lovable generates/updates frontend code. Frontend commits deploy to this repo independently from backend.

---

## nginx / SNI Network Topology

**Production domain:** `https://aimly.agsventurelab.com`

**Port 443 SNI stream dispatcher** (`/etc/nginx/nginx.conf`, `stream {}` block):
- Listens on `:443` with `ssl_preread on` + `proxy_protocol on`
- SNI `storage.googleapis.com` → MTProto proxy (`127.0.0.1:3129` — Fake TLS camouflage)
- All other SNI → nginx HTTPS (`127.0.0.1:8444`)

**aimly vhost** (`/etc/nginx/sites-available/aimly.agsventurelab.com`):
- Listens on `127.0.0.1:8444 ssl proxy_protocol`
- Proxies `http://127.0.0.1:8005` (api container host port)
- Real IP from `proxy_protocol` header

**Full chain:**
```
Client :443 → nginx stream (SNI) → nginx:8444 ssl proxy_protocol → 127.0.0.1:8005 → api:8000
```

**TLS:** Let's Encrypt via `certbot certonly --webroot` ONLY (NOT `--nginx` — would break SNI stream). Auto-renewal via `certbot.timer`. Certificate: `/etc/letsencrypt/live/aimly.agsventurelab.com/`.

**CORS (backend):** `app/main.py` — explicit `cors_origins_list` (env `CORS_ALLOWED_ORIGINS`) + `cors_allowed_origin_regex` for Lovable preview subdomains (`*.lovableproject.com`, `*.lovable.app`). CORS headers also injected on 4xx/5xx error responses via custom exception handlers.

**Headers:** `Authorization`, `X-Workspace-Key`, `Content-Type` allowed.

---

## Data Storage

**Primary database:** PostgreSQL 16 (pgvector image)
- Container: `outreach-platform-db`
- Database: `outreach_platform`
- User: `outreach_user`
- Volume: `postgres_data` (Docker named volume — NEVER `docker compose down -v`)

**Session storage:** Telethon SQLite session files are serialized to `StringSession` strings and stored encrypted in `senders.session_string` column (PostgreSQL). No separate SQLite files in production.

**File/document storage:** KB documents stored as raw bytes in `kb_documents.raw_content BYTEA` — no external object storage.

**Caching:** In-process only (`_TOKEN_CACHE` dict in `app/utils/auth.py` for workspace API keys). No Redis.

**Queue:** PostgreSQL `message_queue` table — no Redis/Celery.

**Backups:** `backup.sh` — `pg_dump --clean --if-exists | gzip`, cron 03:05 daily, 14-day retention at `/root/backups/tg-outreach/`.

---

## Authentication & Identity

**Auth Provider:** Supabase (managed auth + JWT)
- Frontend: email OTP (magic link), PKCE flow, `@supabase/supabase-js`
- Backend: verifies Supabase JWT (ES256/JWKS primary, HS256 fallback)

**Workspace API Keys:**
- Prefix `wsk_` + 40 random hex chars
- bcrypt-hashed (12 rounds), stored in `workspace_api_keys.bcrypt_hash`
- Used by n8n and other integrations via `X-Workspace-Key` header
- 5-min in-process LRU cache to mitigate bcrypt latency

---

## Monitoring & Observability

**Error Tracking:** None (no Sentry/Datadog)

**Logs:**
- API: Python `logging` module, format `%(asctime)s - %(name)s - %(levelname)s - %(message)s`, level `INFO` (overridable via `LOG_LEVEL` env)
- DB: PostgreSQL `log_statement=ddl` + `log_min_duration_statement=1000` → `docker logs outreach-platform-db`
- nginx: `/var/log/nginx/stream_443.log` (stream proxy), standard access log

**LLM Audit:** `llm_calls` table — full prompt/response JSONB, latency_ms, model, conversation_id. Queryable via `GET /api/v1/analytics/llm` endpoint.

**Telemetry:** `POST /api/v1/telemetry/events` — UI posts frontend events (17-event whitelist in `app/routers/telemetry.py::_EVENT_WHITELIST`). Stored in `telemetry_events` table.

**Restriction Audit:** `sender_restriction_events` table (append-only) — all spam_limited/frozen/cleared events since 2026-06-24. Query via `GET /api/v1/senders/{slug}/restriction-events`.

---

## CI/CD & Deployment

**Backend hosting:** VPS DigitalOcean (134.209.239.97)

**Backend deploy:**
```bash
cd /root/apps/aimly/tg-outreach && git pull
docker compose up -d --build api
docker compose up -d --build listener
```

**Frontend hosting:** Cloudflare Workers (edge)

**CI Pipeline:** None (manual deploy)

**Migration deploy:** Automatic on API restart — `app/database.py::_apply_migrations` runs new `migrations/*.sql` files. Add migration file + rebuild api = deployed.

---

## Environment Configuration

**Backend required vars (in `.env`):**
- `DATABASE_URL`
- `TELEGRAM_API_ID`
- `TELEGRAM_API_HASH`
- `ENCRYPTION_KEY`
- `OPENAI_API_KEY`
- `SUPABASE_URL`
- `SUPABASE_JWT_SECRET` (optional — HS256 legacy only)
- `CORS_ALLOWED_ORIGINS`

**Backend optional vars:**
- `DECODO_HOST`, `DECODO_USERNAME`, `DECODO_PASSWORD`, `DECODO_PORTS`
- `OPENAI_MODEL`, `OPENAI_EMBEDDING_MODEL`
- All `KB_*` and `CONTACT_CHECK_*` tuning knobs (see `app/config.py`)

**Frontend required vars (in `.env`, VITE_ prefix):**
- `VITE_SUPABASE_URL`
- `VITE_SUPABASE_ANON_KEY`
- `VITE_BACKEND_URL`

**Secrets location:** `.env` file on server at `/root/apps/aimly/tg-outreach/.env` (gitignored). Frontend secrets in Cloudflare Workers environment variables / Lovable project settings.

---

## Webhooks & Callbacks

**Incoming (to this platform):**
- `POST /api/v1/send` — contact push (n8n or API integrations, authenticated via `X-Workspace-Key`)
- `POST /api/v1/contacts` — bulk contact import
- All authenticated API endpoints accessible via `X-Workspace-Key`

**Outgoing (from this platform):**
- Campaign signal webhooks: `lead`, `handoff`, `finish` — fired to `campaign.webhook_url` or per-event URL. Fire-and-forget, 30s timeout, no HMAC.
- Custom LLM tool webhooks: per `campaigns.tools[].webhook_url`, dispatched during AI function calling.
- Document forwarding webhooks: listener can forward received Telegram documents.

---

*Integration audit: 2026-06-30*
