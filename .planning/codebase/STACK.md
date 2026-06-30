# Technology Stack

**Analysis Date:** 2026-06-30

## Overview

Two-repo system: backend at `/root/apps/aimly/tg-outreach` and frontend at `/root/apps/aimly/aimly-tg-outreach`. They are independent git repos but form a unified product.

---

## Backend

### Language

**Primary:**
- Python 3.11 — all backend code (enforced in `Dockerfile`: `FROM python:3.11-slim`)

### Runtime

**Environment:**
- Python 3.11 inside Docker containers (3 services)

**Package Manager:**
- pip (no lockfile — `requirements.txt` pinned to exact versions)
- Lockfile: present as exact version pins in `requirements.txt`

### Frameworks

**Core:**
- FastAPI 0.109.0 — HTTP API (`app/main.py`)
- Uvicorn 0.27.0 (standard) — ASGI server, launched via `CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]`
- Pydantic v2 (>=2.8,<3.0) — request/response validation, settings
- pydantic-settings >=2.3 — `Settings` class in `app/config.py` reads from `.env`

**ORM & Database:**
- SQLAlchemy 2.0.25 async — all DB operations (`AsyncSession`, `async_sessionmaker`)
- asyncpg 0.29.0 — async PostgreSQL driver
- pgvector 0.4.2 — `pgvector.sqlalchemy.Vector(1536)` for KB chunk embeddings (`app/models/__init__.py`)
- Migrations: raw SQL files in `migrations/`, auto-applied by `app/database.py::_apply_migrations` on startup. Tracking table: `schema_migrations`. NO Alembic.

**Telegram:**
- Telethon 1.42.0 — Telegram MTProto client (sending, listening, onboarding)
- PySocks 1.7.1 — SOCKS5/4/HTTP proxy support for TelegramClient

**AI:**
- openai >=1.40.0,<2.0.0 — `AsyncOpenAI` client in `app/services/ai_engine.py`; also used in `app/services/kb_ingest.py` for embeddings

**Security:**
- cryptography 42.0.0 — Fernet symmetric encryption for Telegram session strings (`app/services/encryption.py`)
- python-jose[cryptography] 3.3.0 — JWT verification (Supabase ES256 via JWKS + HS256 fallback, `app/utils/auth.py`)
- bcrypt >=4.1.0,<5.0 — workspace API key hashing

**RAG / Document Processing:**
- tiktoken 0.13.0 — token-accurate chunking (`cl100k_base` encoding for `text-embedding-3` family)
- pypdf 6.14.2 — PDF text extraction
- python-docx 1.2.0 — DOCX text extraction

**HTTP Client:**
- httpx 0.26.0 — outgoing webhook calls (`app/services/webhook_notify.py`, `app/services/listener.py`)

**Utilities:**
- python-dotenv 1.0.0 — `.env` loading
- python-multipart 0.0.6 — multipart form data (file uploads)
- email-validator 2.1.0 — Pydantic email validation
- qrcode[pil] 7.4.2 — QR code generation for Telegram QR onboarding

**Testing:**
- pytest >=8.0 — test runner (config in `pyproject.toml`)
- pytest-asyncio >=0.23 — async test support (`asyncio_mode = "auto"`, session-scoped loop)

### Key Dependencies File

`/root/apps/aimly/tg-outreach/requirements.txt` — all pinned exact versions.

### Docker Services

Defined in `/root/apps/aimly/tg-outreach/docker-compose.yml`:

| Service | Container | Image | Port |
|---------|-----------|-------|------|
| db | `outreach-platform-db` | `pgvector/pgvector:pg16` | internal only |
| api | `outreach-platform-api` | built from `Dockerfile` | `127.0.0.1:8005:8000` |
| listener | `outreach-platform-listener` | built from `Dockerfile.listener` | none |

**Important:** `pgvector/pgvector:pg16` (not stock `postgres:16`) — provides pre-compiled `vector` extension required by Phase 16 KB.

DB runs with `log_statement=ddl` and `log_min_duration_statement=1000` — all DDL + slow queries logged to `docker logs outreach-platform-db`.

### Configuration

**Settings class:** `app/config.py::Settings` (pydantic-settings `BaseSettings`, reads `.env`)

**Required env vars (backend):**
- `DATABASE_URL` — `postgresql+asyncpg://outreach_user:...@db:5432/outreach_platform`
- `TELEGRAM_API_ID` — Telegram app credentials (from my.telegram.org)
- `TELEGRAM_API_HASH` — Telegram app credentials
- `ENCRYPTION_KEY` — Fernet key for encrypting session strings
- `OPENAI_API_KEY` — OpenAI API key
- `SUPABASE_URL` — Supabase project URL (used for JWKS endpoint)
- `SUPABASE_JWT_SECRET` — Optional, HS256 fallback only
- `CORS_ALLOWED_ORIGINS` — comma-separated allowed origins

**Optional env vars (backend):**
- `OPENAI_MODEL` — default `gpt-5-mini-2025-08-07`
- `OPENAI_EMBEDDING_MODEL` — default `text-embedding-3-small`
- `DECODO_HOST`, `DECODO_USERNAME`, `DECODO_PASSWORD`, `DECODO_PORTS` — Decodo proxy pool
- `KB_CHUNK_MAX_TOKENS` — default 300
- `KB_SEARCH_MAX_DISTANCE` — default 0.8
- `CONTACT_CHECK_BURST_CAP` — default 30
- `RESTRICTION_RECHECK_INTERVAL` — default 21600 (6h)
- `CAMPAIGN_ENQUEUE_TICK_SECONDS` — default 30

**Env file:** `/root/apps/aimly/tg-outreach/.env` (present; never read contents)

### Build

**API Dockerfile:** `/root/apps/aimly/tg-outreach/Dockerfile`
- Base: `python:3.11-slim`
- System deps: `gcc`, `libpq-dev`, `docker.io`
- Copies `app/` + `migrations/` into image
- Runs as non-root `appuser` (uid 1000)

**Listener Dockerfile:** `/root/apps/aimly/tg-outreach/Dockerfile.listener`
- Same base, no `docker.io` dep
- Entrypoint: `python -m app.services.listener`

**Test overlay:** `/root/apps/aimly/tg-outreach/docker-compose.test.yml`
- Ephemeral `pgvector/pgvector:pg16` in tmpfs (`db-test`)
- Overrides DATABASE_URL to `outreach_test`
- Bind-mounts `app/`, `tests/`, `migrations/`, `pyproject.toml`, `lovable-handoff/`

### Background Workers (all asyncio tasks inside API process)

All started in `app/main.py::lifespan`:
- `queue_worker` — rate-limited message send queue (`app/services/queue.py`)
- `warmup_worker` — inter-account AI warmup dialogs (`app/services/warmup.py`)
- `onboarding_cleanup_worker` — expire stale onboarding sessions (`app/services/onboarding_state.py`)
- `contact_check_worker` — checker pool phone resolution (`app/services/contact_check_worker.py`)
- `campaign_enqueue_worker` — campaign→queue generator (`app/services/campaign_enqueue.py`)
- `kb_ingest_worker` — RAG document ingest pipeline (`app/services/kb_ingest_worker.py`)

Listener runs as a **separate container** (`app/services/listener.py`) — not inside the API process.

---

## Frontend

### Language

**Primary:**
- TypeScript 5.8.3 — all frontend code

### Runtime

**Environment:**
- Node.js (via bun)

**Package Manager:**
- bun (lockfile: `/root/apps/aimly/aimly-tg-outreach/bun.lock`)

### Frameworks

**Core:**
- React 19.2.0 — UI framework
- TanStack Start 1.168.26 (`@tanstack/react-start`) — SSR meta-framework
- TanStack Router 1.170.16 (`@tanstack/react-router`) — file-based routing
- TanStack Query 5.83.0 (`@tanstack/react-query`) — server state
- Vite 7.3.1 — build tool
- Tailwind CSS 4.2.1 — utility-first CSS
- shadcn/ui via Radix UI — component library (full Radix primitive set)

**Auth:**
- `@supabase/supabase-js` 2.106.1 — Supabase auth client (OTP email login, PKCE flow; `app/lib/supabase.ts`)

**Forms:**
- react-hook-form 7.71.2 + `@hookform/resolvers` 5.4.0 — forms
- zod 3.24.2 — schema validation

**Charts / UI extras:**
- recharts 2.15.4 — analytics charts
- lucide-react 0.575.0 — icons
- date-fns 4.1.0 — date formatting
- sonner 2.0.7 — toasts
- cmdk 1.1.1 — command palette
- embla-carousel-react 8.6.0 — carousels
- input-otp 1.4.2 — OTP input
- vaul 1.1.2 — drawer

**Deployment / Build:**
- `@cloudflare/vite-plugin` 1.42.2 — Cloudflare Workers build target
- `nitro` 3.0.x — server runtime (Cloudflare edge)
- `wrangler.jsonc` — Cloudflare Workers config (`/root/apps/aimly/aimly-tg-outreach/wrangler.jsonc`)
  - `compatibility_date: "2025-09-24"`, `nodejs_compat` flag
  - `main: "src/server.ts"`

**Dev tooling (Lovable-generated):**
- `@lovable.dev/vite-tanstack-config` 2.6.2 — wraps Vite config; includes TanStack Start, Tailwind, tsConfigPaths, Cloudflare plugin automatically
- eslint 9.x + prettier 3.7.3 — linting/formatting
- `openapi-typescript` 7.13.0 — type generation from `lovable-handoff/openapi.json`

### Frontend Configuration

**Vite config:** `/root/apps/aimly/aimly-tg-outreach/vite.config.ts`
- Uses `@lovable.dev/vite-tanstack-config`; minimal custom config

**Required env vars (frontend, all `VITE_*`):**
- `VITE_SUPABASE_URL` — Supabase project URL
- `VITE_SUPABASE_ANON_KEY` — Supabase anonymous key (public)
- `VITE_BACKEND_URL` — Backend API base URL (e.g. `https://aimly.agsventurelab.com`)

**Env file:** `/root/apps/aimly/aimly-tg-outreach/.env` (present; never read contents)

### Frontend API Communication

`/root/apps/aimly/aimly-tg-outreach/src/lib/api.ts` — central `api()` function:
- Reads Supabase JWT from session via `supabase.auth.getSession()`
- Sends `Authorization: Bearer <jwt>` to backend
- `VITE_BACKEND_URL` sets base URL

---

## Platform Requirements

**Development:**
- Docker + Docker Compose for backend
- bun for frontend

**Production:**
- VPS: DigitalOcean (134.209.239.97)
- Backend: Docker Compose stack
- Frontend: Cloudflare Workers (edge deploy via `wrangler`)
- Domain: `https://aimly.agsventurelab.com` (backend API via nginx)

---

*Stack analysis: 2026-06-30*
