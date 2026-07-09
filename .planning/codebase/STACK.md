# Technology Stack

**Analysis Date:** 2026-07-09

## Languages

**Primary:**
- Python 3.11 - Backend (FastAPI app + Telethon listener), pinned via `Dockerfile` (`FROM python:3.11-slim`) and `Dockerfile.listener`. (Host dev shell reports Python 3.13.7 — irrelevant, containers are the real runtime.)
- SQL (raw, PostgreSQL dialect) - All schema migrations in `migrations/*.sql` — no ORM migration tool. Numbered `NNN_short_name.sql`, auto-applied at API startup.

**Secondary:**
- TypeScript/React - Frontend SPA vendored into this repo at `frontend/` (see note below) — out of primary scope for this backend map, covered briefly here because it now lives inside this git history.

## Runtime

**Environment:**
- Python 3.11-slim (Docker) for both `api` and `listener` services.
- PostgreSQL 16 via `pgvector/pgvector:pg16` image (Postgres 16 + `vector` extension precompiled) — `docker-compose.yml::services.db`.

**Package Manager:**
- pip, dependencies pinned in `requirements.txt` (exact `==` versions for most packages — acts as the lockfile; no `pip-compile`/Poetry/`uv` lock file present).
- Frontend (`frontend/`): `bun` (`bun.lock`, `bunfig.toml`) — built inside a `oven/bun:1` Docker stage since the host has no bun installed (see `deploy-frontend.sh`).

## Frameworks

**Core:**
- FastAPI `0.109.0` - Main HTTP API (`app/main.py`), 20 routers under `app/routers/`.
- Uvicorn `0.27.0` (`[standard]` extras) - ASGI server, `CMD ["uvicorn", "app.main:app", ...]` in `Dockerfile`.
- SQLAlchemy `2.0.25` (async) - ORM models in `app/models/__init__.py`, all access via `AsyncSession`.
- asyncpg `0.29.0` - Async Postgres driver, DSN scheme `postgresql+asyncpg://`.
- Telethon `1.42.0` - Telegram MTProto client library, core of `app/services/telegram.py`, `app/services/checker.py`, `app/services/listener.py`.
- Pydantic `>=2.8,<3.0` / pydantic-settings `>=2.3,<3.0` - Request/response schemas + `app/config.py::Settings` (env-driven config).

**Testing:**
- pytest `>=8.0` + pytest-asyncio `>=0.23` - `tests/` directory, config in `pyproject.toml` (`asyncio_mode = "auto"`, session-scoped event loop).
- Runs only via Docker test-overlay: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest` — never bare `docker compose run --rm api pytest` (would target prod DATABASE_URL). Enforced by a conftest guard (`tests/conftest.py`).

**Build/Dev:**
- Docker Compose - 3 services in prod (`db`, `api`, `listener`), see `docker-compose.yml`; ephemeral 4th (`db-test`) in `docker-compose.test.yml`.
- Vite - Frontend build tool (`frontend/vite.config.ts`), TanStack Start SPA.

## Key Dependencies

**Critical:**
- `telethon==1.42.0` + `PySocks==1.7.1` - All Telegram account automation (sending, listening, onboarding, phone-checking). PySocks backs proxy support for per-sender SOCKS5/proxy assignment.
- `cryptography==42.0.0` (Fernet) - Encrypts Telegram session strings and BYO LLM API keys at rest (`app/services/encryption.py`). One shared `ENCRYPTION_KEY` for both use cases.
- `python-jose[cryptography]==3.3.0` - Supabase JWT verification (ES256 via JWKS + legacy HS256 fallback) in `app/utils/auth.py`. Marked `TODO(v2)` to migrate to PyJWT (jose is in maintenance mode).
- `bcrypt>=4.1.0,<5.0` - Hashing workspace API keys (`wsk_...`) for the `X-Workspace-Key` auth path.
- `openai>=1.40.0,<2.0.0` - Default LLM provider (chat completions via `gpt-5-mini-2025-08-07`, a reasoning model) + embeddings (`text-embedding-3-small`, 1536-dim) for RAG knowledge bases.
- `anthropic>=0.69,<1.0` - Alternate/BYO LLM provider (Phase 18 switchable-provider architecture), `app/services/llm/anthropic_provider.py`.
- `pgvector==0.4.2` - Python client for the Postgres `vector` type; paired with `pgvector/pgvector:pg16` DB image for RAG chunk embeddings (`kb_chunks.embedding Vector(1536)`).
- `tiktoken==0.13.0` - Token counting for KB chunking (`cl100k_base` encoding, `text-embedding-3` family).
- `pypdf==6.14.2`, `python-docx==1.2.0` - Document parsing for knowledge-base ingest (`app/services/kb_ingest.py`).
- `qrcode[pil]==7.4.2` - QR-code generation for Telegram QR-login onboarding flow.

**Infrastructure:**
- `httpx==0.26.0` - Async HTTP client used for: Supabase JWKS fetch, webhook fire-and-forget notifications (`app/services/webhook_notify.py`).
- `python-multipart==0.0.6` - Multipart form parsing (CSV import, bulk account-import ZIP upload, KB document upload).
- `email-validator==2.1.0` - Pydantic email field validation.
- `python-dotenv==1.0.0` - Loads `.env` in local/dev (in Docker, env vars come from compose `environment:` blocks).
- `alembic==1.13.1` - Present in `requirements.txt` but **not used** — project explicitly forbids Alembic; all migrations are raw numbered SQL files auto-applied by `app/database.py::_apply_migrations`. Treat this dependency as vestigial.

## Configuration

**Environment:**
- Config resolved once via `app/config.py::Settings` (pydantic-settings `BaseSettings`), cached with `@lru_cache()` in `get_settings()`. Reads `.env` locally; in Docker, values come from `docker-compose.yml` `environment:` blocks per service.
- Required (no default) vars: `DATABASE_URL`, `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `ENCRYPTION_KEY`, `SUPABASE_URL`.
- Optional/defaulted knobs (all overridable without redeploy): `OPENAI_MODEL` (default `gpt-5-mini-2025-08-07`), `OPENAI_EMBEDDING_MODEL`, `SUPABASE_JWT_SECRET` (legacy HS256 fallback only), `CORS_ALLOWED_ORIGINS`, `CORS_ALLOWED_ORIGIN_REGEX` (Lovable preview subdomains), `DECODO_HOST`/`DECODO_USERNAME`/`DECODO_PASSWORD`/`DECODO_PORTS` (proxy pool), plus ~15 worker-tuning knobs (campaign enqueue tick/batch, follow-up tick, contact-check burst/cooldown/daily-cap/rest/probe-interval/max-backoff, KB ingest poll interval, account-import poll interval, import ZIP size/count caps).
- `.env` file is present on the server (`/root/apps/aimly/tg-outreach/.env`, mode 660) — contents intentionally not read/quoted per operational security policy. Two timestamped backups exist alongside it (`.env.bak.*`).
- `app/config.py::Settings` does **not** set `extra="forbid"` explicitly in the visible model_config, but the test-overlay comments note prod `.env` carries keys that would be rejected by a stricter config — mounting `.env` into test runs is deliberately avoided.

**Build:**
- `Dockerfile` (api) - installs `gcc`, `libpq-dev`, and `docker.io` (the last one is a holdover; a 2026 comment in `docker-compose.yml` notes the host-socket-mount pattern for restarting the listener was removed — `docker.io` in the image may now be vestigial).
- `Dockerfile.listener` - lighter image, no `docker.io`, runs `python -m app.services.listener`.
- `pyproject.toml` - pytest/pytest-asyncio config only (no build-system/packaging metadata — this is not an installable package).
- Frontend: `frontend/vite.config.ts`, `frontend/tsconfig.json`, `frontend/wrangler.jsonc` (Cloudflare Workers config — **currently unused**; see INTEGRATIONS.md note on deploy path).

## Platform Requirements

**Development:**
- Docker + Docker Compose (all services containerized; no documented bare-metal Python dev workflow).
- For frontend work: `bun` (or the Docker `oven/bun:1` image, since the host has no bun installed).

**Production:**
- Single VPS (DigitalOcean, `134.209.239.97`), Docker Compose stack under `/root/apps/aimly/tg-outreach/`.
- API container binds `127.0.0.1:8005:8000` (loopback only — port 8000 is occupied by the legacy, now-stopped `telegram-api` project).
- Public HTTPS via a shared nginx SNI-dispatcher on host `:443` → `nginx:8444 ssl proxy_protocol` → `127.0.0.1:8005`. TLS via `certbot certonly --webroot` only (not `--nginx`, to avoid breaking the SNI stream scheme).
- Frontend static SPA served directly by nginx from `/var/www/aimly` (rsynced by `deploy-frontend.sh`) — no dedicated frontend container in prod despite `wrangler.jsonc` implying a Cloudflare Workers target.
- Postgres data persisted in named Docker volume `postgres_data` — **never** `docker compose down -v` (wipes prod data; recover from `/root/backups/tg-outreach/`).

---

## Note on `frontend/` directory (important — supersedes stale CLAUDE.md text)

As of 2026-07-09, `frontend/` is **vendored directly into this repo's git history** (commit `c176901 Add 'frontend/' from commit '456515f...'`), not merely a sibling checkout. `git remote -v` confirms a second remote `aimly-frontend` → `https://github.com/AGS-Venture-Lab/aimly-tg-outreach.git` alongside `origin` (this backend's repo, `Andrewbruce165/outreach-platform`). Two same-day follow-up commits (`3e42fa6`, `6abac9f`) converted the build to static-SPA mode and added `deploy-frontend.sh` (Docker-bun build → `rsync` to nginx webroot `/var/www/aimly`). The CLAUDE.md description of frontend as a purely separate sibling repo deployed via Cloudflare Workers (`wrangler.jsonc`) reflects the **prior** setup; the current deploy path is host-nginx-served static files, built via Docker (no bun on host). Treat `frontend/` as present-and-current in this checkout, but recognize its own package ecosystem (bun/Vite/React/TanStack Start/shadcn) is independent of the Python backend stack described above.

---

*Stack analysis: 2026-07-09*
