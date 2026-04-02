# External Integrations

**Analysis Date:** 2026-04-02

## APIs & External Services

**Telegram MTProto:**
- Telegram - Core messaging: sending outbound messages, receiving inbound messages, account onboarding (phone/SMS/2FA/QR), contact resolution, file transfers
  - SDK/Client: `telethon==1.42.0` — MTProto library
  - Auth: `TELEGRAM_API_ID` (int) and `TELEGRAM_API_HASH` (str) env vars; per-account session strings stored encrypted in DB
  - Key usage: `app/services/telegram.py`, `app/services/listener.py`, `app/routers/onboarding.py`
  - Rate limits: empirically tuned — 4 msg/min, 20/hour, 150/day per sender (hardcoded in `app/services/queue.py`)

**AI / LLM:**
- OpenAI - GPT-4o-mini AI auto-responder for inbound Telegram messages; also used in warmup conversation generation
  - SDK/Client: `openai>=1.40.0,<2.0.0` — `AsyncOpenAI` client
  - Auth: `OPENAI_API_KEY` env var
  - Key usage: `app/services/ai_engine.py` — supports Function Calling via configurable `webhook_functions` stored in `ai_contexts` table
  - Model referenced in code comments as "GPT-5" but actually gpt-4o-mini at runtime

**Proxy Provider:**
- Decodo ISP (static residential proxies) - Per-sender SOCKS5 proxy assignment to isolate Telegram account IPs
  - SDK/Client: `PySocks==1.7.1` via Telethon proxy config
  - Auth: `DECODO_HOST`, `DECODO_USERNAME`, `DECODO_PASSWORD`, `DECODO_PORTS` env vars
  - Pool stored in `proxy_pool` DB table; assignment tracked in `senders.proxy` JSONB column
  - Key usage: `app/routers/proxy_pool.py`, `app/models/__init__.py` (`ProxyPool` model)
  - Optional — system works without proxy if vars not set

## Data Storage

**Databases:**
- PostgreSQL 16 - Primary and only data store; all application state, queues, sessions, and AI config live here
  - Connection: `DATABASE_URL` env var (`postgresql+asyncpg://...`)
  - Client: SQLAlchemy 2.0 async ORM with `asyncpg` driver (`app/database.py`)
  - Migrations: raw SQL files in `migrations/` — numbered `001_` through `011_`, idempotent (`IF NOT EXISTS`), never Alembic
  - Models defined in `app/models/__init__.py`: `Sender`, `MessageLog`, `ContactCache`, `AIContext`, `MessageQueue`, `Conversation`, `WarmupPool`, `WarmupSession`, `WarmupMessage`, `ProxyPool`, `ContextContactAssignment`

**File Storage:**
- Local filesystem only — downloaded Telegram documents are processed in-memory/temp files and forwarded to external webhooks; no persistent file storage

**Caching:**
- None (no Redis, no Memcached) — queue lives in PostgreSQL `message_queue` table; AI context cache is in-process Python dict in `app/services/ai_engine.py` (5-minute TTL)

## Authentication & Identity

**Auth Provider:**
- Custom API key — single shared `API_KEY` env var; validated via `X-API-Key` header on all protected routes
  - Implementation: `app/routers/auth.py` — `verify_api_key` FastAPI dependency
  - No per-user auth, no JWT, no sessions — single key for all callers (n8n, Lovable frontend, manual)
  - `python-jose` is installed but not currently wired into any endpoint

**Telegram Session Auth:**
- Per-Telethon-account encrypted session strings stored in `senders.session_string` column
  - Encryption: Fernet symmetric cipher (`app/services/encryption.py`) using `ENCRYPTION_KEY` env var
  - Onboarding flow: `app/routers/onboarding.py` — phone number → SMS code → optional 2FA → optional QR code

## Monitoring & Observability

**Error Tracking:**
- None — no Sentry or equivalent configured

**Analytics:**
- None

**Logs:**
- Python `logging` module only — stdout/stderr to Docker container logs
  - Format: `%(asctime)s - %(name)s - %(levelname)s - %(message)s`
  - Level: `INFO` (API), `DEBUG` (listener) — controlled by `LOG_LEVEL` env var for API
  - No log aggregation service

## CI/CD & Deployment

**Hosting:**
- DigitalOcean VPS — Docker Compose stack on `/root/apps/outreach-platform/`
  - Deployment: manual SSH + `git pull` + `docker compose up -d --build`
  - Three services: `db` (postgres:16), `api` (port 8000), `listener`

**CI Pipeline:**
- None — no GitHub Actions or other CI configured

## Environment Configuration

**Required env vars:**
- `DATABASE_URL` - PostgreSQL connection string
- `TELEGRAM_API_ID` - Telegram app ID (from my.telegram.org)
- `TELEGRAM_API_HASH` - Telegram app hash
- `ENCRYPTION_KEY` - Fernet key for session string encryption
- `OPENAI_API_KEY` - OpenAI API access
- `API_KEY` - Shared secret for `X-API-Key` header auth

**Optional env vars:**
- `DECODO_HOST`, `DECODO_USERNAME`, `DECODO_PASSWORD`, `DECODO_PORTS` - Proxy pool (comma-separated ports)
- `LOG_LEVEL` - Defaults to `INFO`

**Secrets location:**
- `.env` file on server at `/root/apps/outreach-platform/.env` (gitignored, no `.env.example` present)

## Webhooks & Callbacks

**Outgoing — queue send callbacks:**
- After each message send (success or failure), the queue worker POSTs to `callback_url` if set on the `MessageQueue` record
  - Pattern: fire-and-forget via `httpx` in `app/services/queue.py`
  - Payload: send result with status, recipient details, error if any

**Outgoing — AI function calling webhooks:**
- AI engine can call external webhook URLs defined in `AIContext.webhook_functions` (JSONB) during OpenAI Function Calling
  - Execution: `app/services/ai_engine.py` `execute_webhook()` — POST via `httpx`
  - Used for: CRM lookups, data enrichment, dynamic responses

**Outgoing — document forwarding:**
- When a Telegram account receives a document/file, listener POSTs it to `document_webhook_url` from the `ai_contexts` table
  - Pattern: fire-and-forget in `app/services/listener.py` `send_document_to_webhook()`
  - Used for: forwarding received documents to n8n or external processing

**Incoming:**
- None — no incoming webhook endpoints exposed (API is called directly via REST, not via webhooks)

---

*Integration audit: 2026-04-02*
*Update when adding/removing external services*
