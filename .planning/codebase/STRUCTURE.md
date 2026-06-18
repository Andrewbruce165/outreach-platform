# Codebase Structure

**Analysis Date:** 2026-06-18

## Repository Overview

This product spans **two sibling repositories** on the same host:

| Repo | Path | Role |
|---|---|---|
| Backend | `/root/apps/aimly/tg-outreach` | Python 3.11 / FastAPI / PostgreSQL / Telethon |
| Frontend | `/root/apps/aimly/aimly-tg-outreach` | TypeScript / TanStack Start / Vite / Bun |

They are independent git repositories. The frontend consumes the backend via HTTP (`VITE_BACKEND_URL`). They are not a monorepo.

---

## Backend Directory Layout

```
/root/apps/aimly/tg-outreach/
├── app/                        # Application package
│   ├── __init__.py
│   ├── config.py               # Pydantic Settings, get_settings() factory
│   ├── database.py             # Engine, AsyncSessionLocal, init_db, _apply_migrations
│   ├── main.py                 # FastAPI app, lifespan, router registration, exception handlers
│   ├── models/
│   │   └── __init__.py         # All SQLAlchemy ORM models (single file)
│   ├── schemas/
│   │   └── __init__.py         # All Pydantic request/response schemas (single file)
│   ├── routers/                # FastAPI APIRouter modules
│   │   ├── agents.py           # /api/v1/agents — AI agent (AIContext) CRUD
│   │   ├── analytics.py        # /api/v1/analytics — funnel, LLM, cards
│   │   ├── campaigns.py        # /api/v1/campaigns — CRUD + lifecycle
│   │   ├── check_contacts.py   # /api/v1/check-contacts — phone/username registration check
│   │   ├── contacts.py         # /api/v1/contacts — contact list + CSV import
│   │   ├── conversations.py    # /api/v1/conversations — inbox + manager mode
│   │   ├── folders.py          # /api/v1/folders — folder CRUD
│   │   ├── health.py           # /api/v1/health — public healthcheck
│   │   ├── onboarding.py       # /api/v1/onboarding — Telegram account onboarding FSM
│   │   ├── proxy_pool.py       # /api/v1/proxy-pool — proxy management (not wired in main)
│   │   ├── queue.py            # /api/v1/queue — queue status inspection
│   │   ├── send.py             # /api/v1/send — direct message enqueue
│   │   ├── senders.py          # /api/v1/senders — sender account CRUD
│   │   ├── telemetry.py        # /api/v1/telemetry/events — UI event ingest
│   │   ├── warmup.py           # /api/v1/warmup — warmup schedule management
│   │   └── workspace.py        # /api/v1/workspace — workspace info + API key CRUD
│   ├── services/               # Domain logic and background workers
│   │   ├── ai_engine.py        # OpenAI GPT completion + signal tools
│   │   ├── campaign_enqueue.py # CampaignEnqueueWorker
│   │   ├── checker.py          # Phone/username Telegram registration checker
│   │   ├── contact_check_worker.py # ContactCheckWorker background loop
│   │   ├── csv_import.py       # CSV parsing + batch contact upsert
│   │   ├── encryption.py       # Fernet session encrypt/decrypt
│   │   ├── listener.py         # Listener container entry point (Telethon + AI reply)
│   │   ├── llm_logger.py       # LLM call audit log writer
│   │   ├── onboarding_state.py # In-memory FSM for Telegram onboarding
│   │   ├── queue.py            # QueueWorker + enqueue_message/enqueue_file helpers
│   │   ├── recontact.py        # protected_conversation_sql predicate
│   │   ├── rotation.py         # get_or_assign_sender (sender load balancing)
│   │   ├── telegram.py         # TelegramService singleton (Telethon client pool)
│   │   ├── template.py         # {{variable}} template rendering
│   │   ├── warmup.py           # WarmupWorker
│   │   └── webhook_notify.py   # notify_signal fire-and-forget HTTP POST
│   └── utils/
│       ├── auth.py             # auth_dep FastAPI dependency + AuthCtx + JWKS cache
│       ├── names.py            # Name formatting helpers
│       └── phone.py            # Phone normalization + contact_identity_key
├── migrations/                 # Raw SQL migration files (auto-applied at startup)
│   ├── _schema_migrations.sql  # Bootstrap: creates schema_migrations tracking table
│   ├── 001_add_unique_constraint_messages.sql
│   ├── 002_add_document_webhook_url.sql
│   ├── ...
│   └── 026_campaign_allow_recontact.sql
├── tests/                      # Pytest test suite
│   ├── conftest.py             # DB setup guard (blocks non-overlay runs) + fixtures
│   ├── test_ai_engine.py
│   └── (other test_*.py files)
├── docker-compose.yml          # Production: db, api, listener
├── docker-compose.test.yml     # Test overlay: ephemeral db-test in tmpfs
├── Dockerfile                  # API container
├── Dockerfile.listener         # Listener container
├── backup.sh                   # pg_dump to /root/backups/tg-outreach/
├── pyproject.toml              # Python project + pytest config
├── requirements.txt            # Pip dependencies
├── CLAUDE.md                   # Developer instructions (source of truth)
└── .planning/                  # GSD planning artifacts
    └── codebase/               # These analysis documents
```

---

## Frontend Directory Layout

```
/root/apps/aimly/aimly-tg-outreach/
├── src/
│   ├── components/             # Reusable React components
│   │   ├── AppSidebar.tsx      # Persistent navigation sidebar
│   │   ├── EditCampaignModal.tsx
│   │   ├── OnboardingFlow.tsx  # Telegram account onboarding multi-step UI
│   │   ├── PulseLogo.tsx
│   │   ├── Topbar.tsx          # Page-level topbar (title + right actions slot)
│   │   └── ui/                 # shadcn/ui generated components (30+ files)
│   ├── hooks/
│   │   └── use-mobile.tsx
│   ├── lib/
│   │   ├── api.ts              # Central HTTP client — api<T>() function
│   │   ├── error-capture.ts    # Error boundary utilities
│   │   ├── error-codes.ts      # Backend error code → user message mapping
│   │   ├── error-page.ts       # SSR 500 HTML renderer
│   │   ├── supabase.ts         # Supabase JS client (browser-only)
│   │   ├── telemetry.ts        # track() event batching + sendBeacon
│   │   └── utils.ts            # cn() class merge helper (shadcn pattern)
│   ├── routes/                 # File-based TanStack Router routes
│   │   ├── __root.tsx          # Root shell + QueryClientProvider + AuthSync
│   │   ├── _authenticated.tsx  # Auth guard layout + AppSidebar
│   │   ├── _authenticated/     # Authenticated sub-routes
│   │   │   ├── index.tsx            → /  (Dashboard)
│   │   │   ├── accounts.tsx         → /accounts
│   │   │   ├── agents.tsx           → /agents
│   │   │   ├── campaigns.index.tsx  → /campaigns
│   │   │   ├── campaigns.new.tsx    → /campaigns/new
│   │   │   ├── campaigns.$id.tsx    → /campaigns/:id
│   │   │   ├── contacts.tsx         → /contacts
│   │   │   ├── inbox.tsx            → /inbox
│   │   │   ├── onboarding.tsx       → /onboarding
│   │   │   └── settings.tsx         → /settings
│   │   ├── auth.callback.tsx   → /auth/callback (PKCE)
│   │   └── login.tsx           → /login
│   ├── styles/
│   │   └── aimly.css           # Custom design tokens + component classes
│   ├── types/
│   │   └── api.ts              # Auto-generated from OpenAPI spec (do NOT edit)
│   ├── routeTree.gen.ts        # Auto-generated route tree (do NOT edit)
│   ├── router.tsx              # createRouter() + QueryClient factory
│   ├── server.ts               # TanStack Start server entry (SSR error handler)
│   ├── start.ts                # createStart() with middleware
│   └── styles.css              # Tailwind base + global resets
├── docs/
│   ├── KNOWLEDGE.md            # Frontend developer knowledge base (AGENTS.md companion)
│   ├── error-codes.md          # Backend error code documentation
│   ├── openapi.json            # Backend OpenAPI spec snapshot (source for type gen)
│   ├── reconciliation.md       # Frontend ↔ backend reconciliation notes
│   ├── screen-build-order.md   # Lovable screen build order reference
│   └── telemetry-events.md     # Telemetry event documentation
├── design-source/              # Source design files from Lovable (JSX mockups)
│   └── project/
│       └── screens/            # Per-screen design reference JSX files
├── AGENTS.md                   # Lovable AI agent instructions
├── components.json             # shadcn/ui config
├── vite.config.ts              # Vite config (delegates to @lovable.dev/vite-tanstack-config)
├── tsconfig.json               # TypeScript config
├── wrangler.jsonc              # Cloudflare Workers deployment config
├── package.json                # Dependencies
└── bun.lock                    # Bun lockfile
```

---

## Directory Purposes (Backend)

**`app/`** — The entire application package. All imports are `from app.*`.

**`app/routers/`** — One file per API resource group. Each file: one `router = APIRouter(prefix="...", tags=[...])`. Register new routers in `app/main.py` via `app.include_router(...)`.

**`app/services/`** — Domain logic isolated from HTTP. Background workers live here as module-level singletons (e.g., `queue_worker = QueueWorker()`). Services import from `app/models`, `app/database`, and each other.

**`app/models/__init__.py`** — All ORM models in one file. Adding a model here + a migration file is the full schema change procedure.

**`app/schemas/__init__.py`** — All Pydantic schemas in one file. Mirror the `*Response`/`*Create`/`*Update` pattern.

**`app/utils/`** — Pure stateless helpers. `auth.py` is special: it contains the `auth_dep` FastAPI dependency used by every router.

**`migrations/`** — Each file runs exactly once (tracked by `schema_migrations`). Never delete or rename applied migrations.

---

## Directory Purposes (Frontend)

**`src/routes/`** — TanStack Router file-based routes. Route file name encodes the URL path: `_authenticated/campaigns.$id.tsx` → `/campaigns/:id`. `_authenticated.tsx` is a layout route (no URL segment).

**`src/components/`** — Shared React components. `ui/` contains shadcn/ui primitives (generated, not hand-written). Custom app components live at the `components/` top level.

**`src/lib/`** — Framework-level utilities. `api.ts` is the single HTTP gateway — do not call `fetch` directly anywhere else.

**`src/types/api.ts`** — Auto-generated from `docs/openapi.json` via `openapi-typescript`. Regenerate when backend API changes: `npx openapi-typescript docs/openapi.json -o src/types/api.ts`.

**`docs/`** — Frontend developer docs and the OpenAPI spec. `openapi.json` is the source of truth for `src/types/api.ts`.

**`design-source/`** — Original JSX mockup screens from Lovable. Reference only; not imported into the app.

---

## Key File Locations

**Backend:**
- API entry point: `app/main.py`
- Auth dependency: `app/utils/auth.py::auth_dep`
- Queue worker: `app/services/queue.py::QueueWorker` + `queue_worker` singleton
- Campaign enqueue: `app/services/campaign_enqueue.py::CampaignEnqueueWorker`
- AI reply: `app/services/ai_engine.py::generate_response`
- Listener container entry: `app/services/listener.py`
- All ORM models: `app/models/__init__.py`
- All Pydantic schemas: `app/schemas/__init__.py`
- Migration files: `migrations/`
- Config env mapping: `app/config.py`

**Frontend:**
- HTTP client: `src/lib/api.ts::api`
- Auth client: `src/lib/supabase.ts`
- API types: `src/types/api.ts`
- Root route: `src/routes/__root.tsx`
- Auth guard layout: `src/routes/_authenticated.tsx`
- Sidebar: `src/components/AppSidebar.tsx`
- Telemetry: `src/lib/telemetry.ts::track`

---

## Naming Conventions

**Backend files:**
- Module files: `snake_case.py`
- Router files named by resource: `campaigns.py`, `senders.py`
- Service files named by concern: `queue.py`, `ai_engine.py`, `telegram.py`

**Backend Python:**
- Classes: `PascalCase` (e.g., `QueueWorker`, `AuthCtx`)
- Functions/methods: `snake_case`
- Constants: `UPPER_SNAKE_CASE` (e.g., `MIN_SEND_INTERVAL`, `FLOOD_HARD_THRESHOLD`)
- Private helpers: leading underscore (e.g., `_apply_migrations`, `_check_rate_limits`)

**Frontend files:**
- Route files: TanStack Router convention — `_layout.tsx`, `resource.index.tsx`, `resource.$param.tsx`
- Component files: `PascalCase.tsx` (e.g., `AppSidebar.tsx`, `EditCampaignModal.tsx`)
- Lib files: `kebab-case.ts` (e.g., `error-codes.ts`, `error-capture.ts`)

**Frontend TypeScript:**
- Components: `PascalCase` function components
- Hooks: `use` prefix (e.g., `use-mobile.tsx`)
- Types derived from API: `type Campaign = components["schemas"]["CampaignResponse"]`

---

## Where to Add New Code

### New Backend API Endpoint

1. Add Pydantic schemas to `app/schemas/__init__.py`
2. Create or extend a router file in `app/routers/`
3. Register the router in `app/main.py` via `app.include_router(...)`
4. If new DB columns needed: add migration `migrations/NNN_short_name.sql` (idempotent)
5. Add/update ORM model in `app/models/__init__.py` if new table

### New Background Worker

1. Create service file in `app/services/` following the `QueueWorker` pattern (class with `start()`/`stop()`, `asyncio.Task`, `_running` flag)
2. Import and register in `app/main.py` lifespan (startup + shutdown)
3. Export a module-level singleton: `worker_name = WorkerName()`

### New Frontend Route (Screen)

1. Create `src/routes/_authenticated/<name>.tsx` (or `<name>.index.tsx` for nested)
2. Run `bun run codegen` (or the equivalent route-tree generation command) to update `src/routeTree.gen.ts`
3. Add nav link to `src/components/AppSidebar.tsx`

### New Frontend API Call

Use `api<T>("/api/v1/resource", opts)` from `src/lib/api.ts`. Never call `fetch` directly. Type the response using `components["schemas"]["..."]` from `src/types/api.ts`.

### New Migration

1. Create `migrations/NNN_short_name.sql` (increment NNN from latest)
2. Write idempotent SQL: `CREATE TABLE IF NOT EXISTS ...`, `ALTER TABLE ... ADD COLUMN IF NOT EXISTS ...`
3. Rebuild API container: `docker compose up -d --build api` — applier runs automatically on startup

---

## Special Directories

**`migrations/`:**
- Generated: No (hand-written)
- Committed: Yes
- Delete/rename: Never (breaks idempotency tracking)

**`src/routeTree.gen.ts`:**
- Generated: Yes (TanStack Router codegen)
- Committed: Yes (required for builds)
- Edit manually: No

**`src/types/api.ts`:**
- Generated: Yes (openapi-typescript from `docs/openapi.json`)
- Committed: Yes
- Edit manually: No

**`src/components/ui/`:**
- Generated: Yes (shadcn/ui CLI)
- Committed: Yes
- Edit manually: Only if customizing a specific primitive

**`.planning/codebase/`:**
- Generated: Yes (GSD map-codebase command)
- Committed: Yes
- Edit manually: Only to add notes; overwritten on next `map-codebase`

---

*Structure analysis: 2026-06-18*
