# Technology Stack

**Analysis Date:** 2026-06-18

---

## Backend

### Languages

**Primary:**
- Python 3.11 — All API, services, workers, migrations

### Runtime

**Environment:**
- Python 3.11-slim (Docker image: `python:3.11-slim`)

**Containers:** 3 services via Docker Compose (`/root/apps/aimly/tg-outreach/docker-compose.yml`)
- `outreach-platform-db` — PostgreSQL 16
- `outreach-platform-api` — FastAPI app, bound to `127.0.0.1:8005:8000`
- `outreach-platform-listener` — Telethon listener daemon (separate `Dockerfile.listener`)

**Package Manager:**
- pip with `/root/apps/aimly/tg-outreach/requirements.txt`
- Pinned exact versions; no lockfile

### Frameworks

**Core:**
- FastAPI 0.109.0 — HTTP API framework, OpenAPI docs at `/docs`
- Uvicorn 0.27.0 (standard extras) — ASGI server

**ORM / Database:**
- SQLAlchemy 2.0.25 async — ORM with `AsyncSession` + `async_sessionmaker` (`/root/apps/aimly/tg-outreach/app/database.py`)
- asyncpg 0.29.0 — PostgreSQL async driver (used by SQLAlchemy and the raw migration applier)
- Raw SQL migrations in `/root/apps/aimly/tg-outreach/migrations/*.sql` — auto-applied at startup via `app/database.py::_apply_migrations`; tracked in `schema_migrations` table; idempotency required
- NOTE: `alembic==1.13.1` is in `requirements.txt` but is **NOT used** — explicitly forbidden per `CLAUDE.md`

**Validation:**
- Pydantic 2.8+ — request/response schemas, settings (`/root/apps/aimly/tg-outreach/app/schemas/`)
- pydantic-settings 2.3+ — `app/config.py::Settings` via `BaseSettings`
- email-validator 2.1.0

**Testing:**
- pytest 8.0+ — test runner
- pytest-asyncio 0.23+ — async test support, `asyncio_mode = "auto"`, session-scoped event loop
- Config: `/root/apps/aimly/tg-outreach/pyproject.toml` (`[tool.pytest.ini_options]`)
- Test isolation: `docker-compose.test.yml` overlay with ephemeral PostgreSQL in tmpfs

**Build / Deployment:**
- Docker Compose — dev and production
- `/root/apps/aimly/tg-outreach/Dockerfile` — API container (`python:3.11-slim`, non-root `appuser`)
- `/root/apps/aimly/tg-outreach/Dockerfile.listener` — Listener container

### Key Dependencies

**Telegram MTProto:**
- telethon 1.42.0 — Telegram MTProto client for onboarding, sending, listening, warmup, phone checking
- PySocks 1.7.1 — SOCKS5 proxy support for Telethon (Decodo proxy pool)

**AI / LLM:**
- openai 1.40.0+ — `AsyncOpenAI` client; default model `gpt-5-mini-2025-08-07` (overridable via `OPENAI_MODEL` env); function calling / tool use supported
- Used in: `/root/apps/aimly/tg-outreach/app/services/ai_engine.py` (response generation + function calling), `/root/apps/aimly/tg-outreach/app/services/warmup.py` (AI warmup conversations)
- OpenAI Whisper API used for audio transcription via httpx

**Security:**
- cryptography 42.0.0 — Fernet symmetric encryption of Telethon session strings in DB (`/root/apps/aimly/tg-outreach/app/services/encryption.py`)
- python-jose 3.3.0 — JWT decode/verify (`/root/apps/aimly/tg-outreach/app/utils/auth.py`): primary ES256 via JWKS endpoint, fallback HS256 with `SUPABASE_JWT_SECRET`
- bcrypt 4.1.0+ — workspace API key hashing (`wsk_` prefix format)
- httpx 0.26.0 — async HTTP client for JWKS fetch (auth), webhook fire-and-forget, external callbacks

**Utilities:**
- python-dotenv 1.0.0 — `.env` loading in development
- qrcode 7.4.2 (with PIL) — QR code generation for Telegram QR-login onboarding (`/root/apps/aimly/tg-outreach/app/routers/onboarding.py`)
- python-multipart 0.0.6 — multipart form upload for CSV import

### Configuration

**Environment:**
- Loaded via pydantic-settings `BaseSettings` in `/root/apps/aimly/tg-outreach/app/config.py`
- Dev: `.env` file; Prod: Docker Compose `environment:` block

**Required backend env vars:**
- `DATABASE_URL` — `postgresql+asyncpg://outreach_user:...@db:5432/outreach_platform`
- `TELEGRAM_API_ID` — Telegram MTProto app ID
- `TELEGRAM_API_HASH` — Telegram MTProto app hash
- `ENCRYPTION_KEY` — Fernet key for Telethon session string encryption
- `OPENAI_API_KEY` — OpenAI API key
- `SUPABASE_URL` — Supabase project URL (JWKS endpoint derivation + CORS)
- `SUPABASE_JWT_SECRET` — Optional; legacy HS256 JWT fallback only

**Optional backend env vars:**
- `DECODO_HOST`, `DECODO_USERNAME`, `DECODO_PASSWORD`, `DECODO_PORTS` — Decodo ISP residential proxy pool
- `OPENAI_MODEL` — Override LLM model (default: `gpt-5-mini-2025-08-07`)
- `CORS_ALLOWED_ORIGINS` — Comma-separated allowlist (default: `http://localhost:5173`)
- `CORS_ALLOWED_ORIGIN_REGEX` — Regex for Lovable preview subdomains (default accepts `*.lovableproject.com` and `*.lovable.app`)
- `CAMPAIGN_ENQUEUE_TICK_SECONDS` — Worker tick interval (default: 30)
- `CAMPAIGN_ENQUEUE_BATCH_SIZE` — Contacts per campaign per tick (default: 500)

### Platform Requirements

**Development:**
- Docker + Docker Compose required
- `.env` file with all required vars
- Tests ONLY via: `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest`
- NEVER: `docker compose run --rm api pytest` (would target prod DB)

**Production:**
- VPS DigitalOcean (134.209.239.97)
- TLS via `certbot certonly --webroot` (NOT `--nginx`) — SNI stream block owns port 443
- nginx reverse proxy chain: `:443 → SNI stream → nginx:8444 ssl proxy_protocol → 127.0.0.1:8005 → api:8000`
- API domain: `https://aimly.agsventurelab.com`
- Deploy: `cd /root/apps/aimly/tg-outreach && git pull && docker compose up -d --build api`
- Listener rebuild: `docker compose up -d --build listener`
- Backups: `/root/apps/aimly/tg-outreach/backup.sh` via cron `5 3 * * *`, 14-day retention in `/root/backups/tg-outreach/`

---

## Frontend

### Languages

**Primary:**
- TypeScript 5.8.3 — All source files
- TSX — React components

### Runtime

**Environment:**
- Bun — package manager and dev runtime
- Node.js 22 (types only, `@types/node ^22`)
- Browser + Cloudflare Workers (SSR)

**Package Manager:**
- bun; lockfile: `bun.lockb` present
- Supply-chain guard in `/root/apps/aimly/aimly-tg-outreach/bunfig.toml`: packages published <24h are blocked globally (bypassed for `@lovable.dev/vite-tanstack-config`)

### Frameworks

**Core:**
- React 19.2.0 — UI library
- TanStack Start 1.167.50 — SSR-capable React meta-framework
- TanStack Router 1.168.25 — Type-safe file-based routing; routes in `/root/apps/aimly/aimly-tg-outreach/src/routes/`
- TanStack Query 5.83.0 — Server state / data fetching; cache keys `['<resource>', ...params]`
- Vite 7.3.1 — Build tool; config in `/root/apps/aimly/aimly-tg-outreach/vite.config.ts`
- `@lovable.dev/vite-tanstack-config` 2.3.2 — Lovable-managed Vite config wrapper; **do NOT add** TanStack, React, Tailwind, Cloudflare, or path alias plugins manually — already included

**Styling:**
- Tailwind CSS 4.2.1 — Utility-first CSS (`@tailwindcss/vite` plugin)
- shadcn/ui — Component library; style: `new-york`, base: `slate`, CSS variables enabled; config: `/root/apps/aimly/aimly-tg-outreach/components.json`
- Radix UI — Full suite of `@radix-ui/react-*` headless primitives
- lucide-react 0.575.0 — Icons
- class-variance-authority, clsx, tailwind-merge — className composition
- Design tokens: `/root/apps/aimly/aimly-tg-outreach/src/styles/aimly.css` (ingested from `design-source/project/styles.css`)
- AI accent color: `--ai-purple #8774e1` — reserved for AI-only UI elements only

**Forms:**
- react-hook-form 7.71.2 + `@hookform/resolvers` — All forms
- zod 3.24.2 — Validation schemas (location: `src/lib/validators/*.ts`)

**Charts:**
- recharts 2.15.4 — Dashboard analytics charts

**UI Components (additional):**
- sonner 2.0.7 — Toast notifications
- cmdk 1.1.1 — Command palette (v2 disabled stub)
- embla-carousel-react 8.6.0
- react-day-picker 9.14.0 + date-fns 4.1.0
- react-resizable-panels 4.6.5 — 3-pane inbox layout
- vaul 1.1.2 — Drawer
- input-otp 1.4.2 — OTP input for SMS verification

**Build / Deployment:**
- Cloudflare Workers — SSR runtime
- `wrangler.jsonc` — Cloudflare config: name `tanstack-start-app`, compatibility `2025-09-24`, `nodejs_compat` flag
- `@cloudflare/vite-plugin` 1.25.5 — Cloudflare build integration
- nitro 3.0.260603-beta — Server bundler (TanStack Start dependency)
- SSR entry: `/root/apps/aimly/aimly-tg-outreach/src/server.ts`

**Dev Tools:**
- ESLint 9.32.0 + typescript-eslint 8.56.1 + react-hooks + react-refresh plugins
- Prettier 3.7.3 + eslint-config-prettier + eslint-plugin-prettier
- `openapi-typescript` 7.13.0 (devDep) — Generates `/root/apps/aimly/aimly-tg-outreach/src/types/api.ts` from backend OpenAPI spec
- vite-tsconfig-paths 6.0.2 — TypeScript path aliases in Vite

**Generation Platform:**
- Lovable — AI-assisted frontend generation; template: `tanstack_start_ts_2026-05-12`
- Build rules: `/root/apps/aimly/aimly-tg-outreach/AGENTS.md`

**TypeScript Config (`/root/apps/aimly/aimly-tg-outreach/tsconfig.json`):**
- Target: ES2022, module: ESNext, strict: true
- Module resolution: Bundler
- Path alias: `@/*` → `./src/*`

### Key Dependencies

**Auth:**
- `@supabase/supabase-js` 2.106.1 — browser-only Supabase client (`/root/apps/aimly/aimly-tg-outreach/src/lib/supabase.ts`); PKCE flow; SSR stub proxy that throws on access

### Configuration

**Required frontend env vars (VITE_ prefix, in `/root/apps/aimly/aimly-tg-outreach/.env`):**
- `VITE_BACKEND_URL` — Backend API base URL (e.g., `https://aimly.agsventurelab.com`)
- `VITE_SUPABASE_URL` — Supabase project URL
- `VITE_SUPABASE_ANON_KEY` — Supabase anon (public) key

### Platform Requirements

**Development:**
- `bun install` + `bun run dev` (Vite dev server)

**Production:**
- Deployed to Cloudflare Workers via `wrangler`
- Frontend repo: `/root/apps/aimly/aimly-tg-outreach`

---

*Stack analysis: 2026-06-18*
