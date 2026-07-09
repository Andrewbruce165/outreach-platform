# External Integrations

**Analysis Date:** 2026-07-09

## APIs & External Services

**Telegram (core product surface):**
- Telethon MTProto client - `app/services/telegram.py` (send/receive, onboarding, spambot checks, profile sync), `app/services/checker.py` (phone-number resolution via dedicated "checker" accounts), `app/services/listener.py` (standalone process, one listener per active sender, AI auto-reply pipeline).
- Auth: per-sender encrypted Telethon session string (`senders.session_string`, Fernet-encrypted via `ENCRYPTION_KEY`) + platform-wide `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` (single Telegram app registration shared by all tenant accounts).
- Onboarding flows: phone/SMS code, 2FA password, QR-login (`qrcode[pil]`) — `app/routers/onboarding.py`, `app/services/onboarding_state.py`.
- Known semantics caveat: `contacts_cache.is_registered=false` means "not resolvable by phone by a stranger checker account", not "no Telegram account exists" — see docstring in `app/services/checker.py` and CLAUDE.md for full false-negative/shadow-ban history.

**LLM Providers (switchable, Phase 18):**
- OpenAI - `app/services/llm/openai_provider.py`, default chat model `gpt-5-mini-2025-08-07` (reasoning model, `OPENAI_MODEL` env override) + embeddings model `text-embedding-3-small` (1536-dim, `OPENAI_EMBEDDING_MODEL` override). SDK: `openai` (AsyncOpenAI). Auth: platform `OPENAI_API_KEY` env var, or per-workspace BYO key (encrypted, `app/services/llm/resolve.py`).
- Anthropic (Claude) - `app/services/llm/anthropic_provider.py`, BYO-only alternate provider (no platform-wide default key configured in compose). SDK: `anthropic` (AsyncAnthropic). Handles role-alternation coalescing that OpenAI doesn't require.
- Resolution policy (`app/services/llm/resolve.py::resolve_llm_config`): absent/invalid workspace `llm_settings` row → platform default (OpenAI). A runtime key-level error on a BYO key degrades to `key_source="fallback"` (platform default) without swapping providers on transient errors (429/5xx), to avoid leaking client traffic onto the platform bill.
- Used by: `app/services/ai_engine.py` (auto-reply answerer), `app/services/warmup.py` (warmup conversation generation), `app/services/kb_ingest.py` (embeddings for RAG).

**Decodo (residential proxy provider):**
- `ProxyPool` model (`app/models/__init__.py`, table `proxy_pool`) - static ISP residential proxies, one row per host:port, assignable to a sender (`assigned_to_sender_id`).
- Config: `DECODO_HOST`, `DECODO_USERNAME`, `DECODO_PASSWORD`, `DECODO_PORTS` (comma-separated port list) — all optional env vars in `app/config.py`.
- Client-side: `PySocks` (SOCKS5) via Telethon's proxy support.
- Note (from project memory): public Decodo API exposes no ISP list; ports wrap mod-100 beyond subscription tier — dedup by IP, not port.

## Data Storage

**Databases:**
- PostgreSQL 16 (`pgvector/pgvector:pg16` image) - sole datastore, container `outreach-platform-db`.
  - Connection: `DATABASE_URL` (async DSN, `postgresql+asyncpg://outreach_user:...@db:5432/outreach_platform`).
  - Client/ORM: SQLAlchemy 2.0 async (`AsyncSession`), raw SQL for all migrations and much of the query layer (`text(...)` used heavily alongside ORM models).
  - `vector` extension enabled (`CREATE EXTENSION vector`) for RAG embeddings (`kb_chunks.embedding Vector(1536)`) — must run before `create_all` on a fresh DB (see `app/database.py::init_db`).
  - DDL/slow-query auditing: Postgres started with `log_statement=ddl` + `log_min_duration_statement=1000` (anti-drift measure after a 2026-05-26 accidental-DROP incident).

**File Storage:**
- Local filesystem only — no S3/object storage integration detected. Uploaded artifacts (campaign attachments, KB source documents, bulk account-import ZIPs) are processed server-side and either stored as DB bytes/text or transient temp files; no external blob storage SDK in `requirements.txt`.

**Caching:**
- None (no Redis/Memcached). In-process, per-container caches only:
  - Workspace-API-key validation cache (`app/utils/auth.py::_TOKEN_CACHE`, 5-min TTL, bounded 1024 entries) — avoids re-running bcrypt on every request.
  - Supabase JWKS cache (`app/utils/auth.py::_JWKS_CACHE`, 1h TTL, refetch on `kid` miss).
  - Both caches are per-process, not shared across `api` container replicas (acceptable at current single-container scale).

## Authentication & Identity

**Auth Provider:**
- Supabase Auth (JWT-based) - primary auth for the UI/frontend path.
  - Verification: fetches project JWKS (`${SUPABASE_URL}/auth/v1/.well-known/jwks.json`) and verifies ES256 signatures (Supabase's default since Oct 2025). Legacy HS256 fallback via `SUPABASE_JWT_SECRET` for projects that haven't migrated.
  - Implementation: `app/utils/auth.py::auth_dep` (FastAPI dependency), `_decode_supabase_jwt`, `_get_jwk_for_kid`.
  - Lazy workspace creation: first valid JWT for a new `sub` auto-creates a `Workspace` + `UserWorkspace` row inside one transaction, race-guarded by a DB `UNIQUE(supabase_user_id)` constraint + `ON CONFLICT DO NOTHING` + orphan cleanup.
- Workspace API keys (`wsk_...`) - secondary auth path for machine integrations (n8n, ad-hoc scripts).
  - Storage: bcrypt hash + 12-char prefix in `workspace_api_keys` table; verified via `X-Workspace-Key` header.
  - `app/utils/auth.py::_verify_api_key` — constant-time prefix compare (`hmac.compare_digest`) + bcrypt check (offloaded to a thread), 5-min in-process cache on success.

## Monitoring & Observability

**Error Tracking:**
- None (no Sentry/Rollbar/etc.). All errors surface via Python `logging` + FastAPI global exception handlers in `app/main.py` (`RequestValidationError`, `StarletteHTTPException`, catch-all `Exception` → 500 with CORS headers preserved).

**Logs:**
- Standard `logging` module, `basicConfig(level=INFO)` in `app/main.py`. Docker's default log driver captures stdout/stderr (rotation governed by host-level `daemon.json`, per top-level `/root/CLAUDE.md`).
- Postgres DDL/slow-query logging (see Data Storage) is the primary DB-side observability signal — `docker logs outreach-platform-db`.

## CI/CD & Deployment

**Hosting:**
- Single DigitalOcean VPS (`134.209.239.97`), Docker Compose. No Kubernetes, no managed PaaS.
- Backend: `docker compose up -d --build api` / `... listener` after `git pull` — manual deploy, no automated pipeline detected (no `.github/workflows/` observed in this repo tree).
- Frontend: `deploy-frontend.sh` — builds the TanStack Start SPA in a throwaway `oven/bun:1` Docker container (`bun install --frozen-lockfile && bun run build`), then `rsync`s `dist/client/` to `/var/www/aimly` on the same host, served by nginx directly. No CDN/edge deploy despite `frontend/wrangler.jsonc` (Cloudflare Workers config) being present — that config appears to reflect a prior/parallel deploy target, not the currently exercised path.

**CI Pipeline:**
- None detected in this repo (`Andrewbruce165/outreach-platform`). Tests are run manually via the Docker test-overlay command (see STACK.md); no evidence of automated test runs on push.

## Environment Configuration

**Required env vars:**
- `DATABASE_URL`, `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `ENCRYPTION_KEY`, `SUPABASE_URL`.

**Frequently-used optional vars:**
- `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_EMBEDDING_MODEL`, `SUPABASE_JWT_SECRET`, `CORS_ALLOWED_ORIGINS`, `CORS_ALLOWED_ORIGIN_REGEX`, `DECODO_HOST`/`DECODO_USERNAME`/`DECODO_PASSWORD`/`DECODO_PORTS`, plus ~15 worker-cadence knobs (see STACK.md Configuration section for the full list).

**Secrets location:**
- `/root/apps/aimly/tg-outreach/.env` (mode 660, not committed — `.gitignore` excludes `.env` and `.env.*.bak`). Values injected into containers via `docker-compose.yml` `environment:` blocks referencing `${VAR}` interpolation from the host `.env`. Contents were not read as part of this analysis (forbidden-files policy).

## Webhooks & Callbacks

**Incoming:**
- `POST /telemetry/events` - UI telemetry ingest (`app/routers/telemetry.py`), whitelist-gated (`_EVENT_WHITELIST`, 17 named events e.g. `signup_completed`, `campaign_launched`, `agent_created`) — unknown event names get a 400 `UNKNOWN_EVENT` rather than silently persisting.
- No inbound webhook receivers from third-party services (no Stripe/GitHub/etc. webhook handlers detected).

**Outgoing:**
- Fire-and-forget campaign-signal webhooks - `app/services/webhook_notify.py`. Triggered by `app/services/ai_engine.py::_handle_builtin_signal` on built-in campaign signals (lead / handoff / finish). Uses `httpx.AsyncClient` with a 30s timeout, catches all exceptions (never blocks the AI response path), includes the last 20 messages of conversation history as an excerpt. **No HMAC signing** on outgoing payloads (explicitly deferred to a future version per the module docstring).
- This is the integration point historically consumed by n8n workflows on the AGS side (per top-level `/root/CLAUDE.md` "n8n Workflows" section) — workspace API keys (`X-Workspace-Key`) are the intended auth mechanism for n8n-side push-in integrations (e.g. contact import).

---

*Integration audit: 2026-07-09*
