# Codebase Structure

**Analysis Date:** 2026-06-30

## Repository Layout

This is a two-repo system. Backend commits go to `Andrewbruce165/outreach-platform`; frontend commits go to `AGS-Venture-Lab/aimly-tg-outreach`. They share no git history but form one product.

---

## Backend: `/root/apps/aimly/tg-outreach/`

```
tg-outreach/
├── app/                        # All Python application code
│   ├── main.py                 # FastAPI app factory, lifespan, CORS, router registration
│   ├── config.py               # Pydantic Settings (env vars, @lru_cache singleton)
│   ├── database.py             # Engine, session factory, init_db(), _apply_migrations()
│   ├── models/
│   │   └── __init__.py         # ALL SQLAlchemy ORM models (single file, ~860 lines)
│   ├── routers/                # One file per feature area (FastAPI APIRouter)
│   │   ├── agents.py           # AI context (agent) CRUD
│   │   ├── analytics.py        # Read-only analytics + LLM call log endpoints
│   │   ├── campaigns.py        # Campaign CRUD + lifecycle (start/pause/resume/finish)
│   │   ├── check_contacts.py   # Manual recheck trigger
│   │   ├── contacts.py         # Contact CRUD + CSV import + folder management
│   │   ├── conversations.py    # Inbox list, send from UI, manager-mode toggle
│   │   ├── folders.py          # Folder CRUD
│   │   ├── health.py           # GET /api/v1/health
│   │   ├── knowledge_bases.py  # KB CRUD, document upload, search endpoint
│   │   ├── onboarding.py       # Telegram account onboarding (phone/SMS/2FA/QR)
│   │   ├── proxy_pool.py       # Proxy pool CRUD + assignment
│   │   ├── queue.py            # Queue inspection endpoints
│   │   ├── send.py             # POST /api/v1/send (ad-hoc send, not campaign)
│   │   ├── senders.py          # Sender CRUD + restriction-events endpoint
│   │   ├── telemetry.py        # UI telemetry event ingest (whitelist-gated)
│   │   ├── warmup.py           # Warmup pool + settings CRUD
│   │   └── workspace.py        # Workspace info + API key management
│   ├── schemas/
│   │   ├── __init__.py         # Pydantic request/response schemas (Campaign, Sender, etc.)
│   │   └── knowledge_bases.py  # KB-specific Pydantic schemas
│   ├── services/               # Business logic + background workers
│   │   ├── ai_engine.py        # OpenAI chat.completions wrapper + built-in signal tools
│   │   ├── campaign_enqueue.py # CampaignEnqueueWorker (folder → message_queue)
│   │   ├── checker.py          # CheckerService — batch phone resolve via checker accounts
│   │   ├── contact_check_worker.py  # ContactCheckWorker — drives checker pool
│   │   ├── csv_import.py       # CSV parse, column mapping, contact insert
│   │   ├── encryption.py       # AES encrypt/decrypt for Telegram session strings
│   │   ├── failover.py         # Cold-backlog failover (stuck jobs recovery)
│   │   ├── kb_ingest.py        # Text extract, chunk, embed (used by worker)
│   │   ├── kb_ingest_worker.py # KnowledgeIngestWorker (claims pending kb_documents)
│   │   ├── kb_search.py        # pgvector cosine-distance search over kb_chunks
│   │   ├── listener.py         # Telegram listener (runs as __main__ in listener container)
│   │   ├── llm_logger.py       # Writes LLMCall audit rows
│   │   ├── onboarding_state.py # OnboardingCleanupWorker (expires stale sessions)
│   │   ├── queue.py            # QueueWorker — rate-limited outbound send loop
│   │   ├── rebalance.py        # Sender load-rebalance across campaigns
│   │   ├── recontact.py        # Protected conversation SQL helpers (recontact policy)
│   │   ├── restriction_audit.py # Writes SenderRestrictionEvent rows (append-only)
│   │   ├── rotation.py         # get_or_assign_sender() — per-campaign sender picker
│   │   ├── telegram.py         # TelegramService singleton (client cache, send, resolve)
│   │   ├── template.py         # render_template() — {{variable}} substitution
│   │   ├── warmup.py           # WarmupWorker — inter-account AI warmup sessions
│   │   └── webhook_notify.py   # notify_signal() — fires campaign webhooks
│   ├── utils/
│   │   ├── auth.py             # auth_dep FastAPI Depends (JWT + API key), AuthCtx
│   │   ├── names.py            # Name normalization helpers
│   │   └── phone.py            # Phone normalize, is_username_key(), contact_identity_key()
│   └── data/
│       └── control_set_known_live.txt  # 49 known-live phone numbers for checker health probe
├── migrations/                 # Raw-SQL migrations (auto-applied at startup)
│   ├── _schema_migrations.sql  # Bootstrap: creates schema_migrations tracking table
│   ├── 001_…sql                # Sequential numbered migrations (001–042 as of 2026-06-30)
│   └── 042_kb_id_server_defaults.sql  # Latest as of analysis date
├── tests/                      # pytest test suite (~120 test files)
│   ├── conftest.py             # DB setup, drop-guard, test client fixtures
│   ├── utils/
│   │   └── openai_mocks.py     # OpenAI response stubs
│   └── test_*.py               # Test files (one per feature/phase)
├── lovable-handoff/            # Specs consumed by Lovable frontend generator
│   ├── openapi.json            # OpenAPI spec (source of truth for frontend API calls)
│   ├── AGENTS.md               # Agent-screen contract
│   ├── KNOWLEDGE.md            # Knowledge base screen contract
│   ├── PRD.md                  # Product requirements for Lovable
│   └── types/                  # Shared TypeScript type stubs
├── docs/                       # Internal developer docs
├── Dockerfile                  # API container (python:3.11-slim, uvicorn)
├── Dockerfile.listener         # Listener container (python:3.11-slim, python -m app.services.listener)
├── docker-compose.yml          # Production: db + api + listener
├── docker-compose.test.yml     # Test overlay: adds ephemeral db-test in tmpfs, overrides DATABASE_URL
├── pyproject.toml              # pytest config, dependency groups
├── requirements.txt            # Pinned Python dependencies
├── backup.sh                   # pg_dump script (cron 03:05 daily)
└── CLAUDE.md                   # Project instructions for Claude Code
```

---

## Frontend: `/root/apps/aimly/aimly-tg-outreach/`

```
aimly-tg-outreach/
├── src/
│   ├── routes/                 # TanStack Router file-based routes
│   │   ├── __root.tsx          # Root shell: QueryClientProvider, AuthSync, Toaster
│   │   ├── _authenticated.tsx  # Auth guard layout: checks Supabase session, renders AppSidebar
│   │   ├── _authenticated/     # All protected screens
│   │   │   ├── index.tsx       # Dashboard (/)
│   │   │   ├── accounts.tsx    # Sender accounts + onboarding (/accounts)
│   │   │   ├── agents.tsx      # AI contexts CRUD (/agents)
│   │   │   ├── campaigns.index.tsx   # Campaign list (/campaigns)
│   │   │   ├── campaigns.new.tsx     # Campaign builder (/campaigns/new)
│   │   │   ├── campaigns.$id.tsx     # Campaign detail + pool health (/campaigns/:id)
│   │   │   ├── contacts.tsx    # Contact list + CSV import (/contacts)
│   │   │   ├── inbox.tsx       # Conversation inbox + AI toggle (/inbox)
│   │   │   ├── knowledge-bases.index.tsx  # KB list (/knowledge-bases)
│   │   │   ├── knowledge-bases.$id.tsx    # KB documents + search test (/knowledge-bases/:id)
│   │   │   ├── onboarding.tsx  # Account onboarding wizard (/onboarding)
│   │   │   ├── settings.tsx    # Workspace settings + API keys (/settings)
│   │   │   └── warmup.tsx      # Warmup pool + settings (/warmup)
│   │   ├── auth.callback.tsx   # Supabase OAuth callback (/auth/callback)
│   │   └── login.tsx           # Login screen (/login)
│   ├── components/
│   │   ├── AppSidebar.tsx      # Navigation sidebar with workspace switcher
│   │   ├── EditCampaignModal.tsx  # Campaign edit modal
│   │   ├── OnboardingFlow.tsx  # Multi-step account onboarding component
│   │   ├── StageEditor.tsx     # Dialogue-stage editor for campaign flow
│   │   ├── Topbar.tsx          # Page topbar
│   │   ├── PulseLogo.tsx       # Animated logo
│   │   └── ui/                 # shadcn/ui components (accordion, button, dialog, …)
│   ├── lib/
│   │   ├── api.ts              # Central fetch wrapper (getAccessToken → Bearer header, error envelope)
│   │   ├── supabase.ts         # Supabase JS client (lazy, SSR-safe stub)
│   │   ├── telemetry.ts        # Client-side telemetry event sender → POST /api/v1/telemetry/events
│   │   ├── error-capture.ts    # Error boundary capture utilities
│   │   ├── error-codes.ts      # Error envelope parsers
│   │   ├── error-page.ts       # Error page utilities
│   │   └── utils.ts            # cn() and other utility helpers
│   ├── hooks/
│   │   └── use-mobile.tsx      # Responsive breakpoint hook
│   ├── routeTree.gen.ts        # Auto-generated route tree (TanStack Router codegen)
│   ├── router.tsx              # createRouter() factory with QueryClient context
│   └── server.ts               # Nitro/Cloudflare server entry
├── design-source/              # Reference designs (JSX mockups, screenshots) — not built code
├── lovable-handoff/            # Consumed by Lovable during generation (symlinked from backend)
├── dist/                       # Build output (client/ + server/) — Cloudflare Pages deploy target
├── wrangler.jsonc              # Cloudflare Workers/Pages deploy config
├── components.json             # shadcn/ui config
├── bunfig.toml                 # Bun package manager config
├── bun.lock                    # Lockfile
└── AGENTS.md                   # Instructions for Lovable AI generation
```

---

## Key File Locations

**Backend entry points:**
- `app/main.py` — FastAPI app, lifespan, router registration
- `app/services/listener.py` — listener container entry (`__main__`)
- `app/database.py` — `init_db()` + `_apply_migrations()` (startup)

**Auth:**
- `app/utils/auth.py` — `auth_dep` Depends, `AuthCtx`, JWT verify, API key verify, JWKS cache

**Core workers (all started in `app/main.py` lifespan):**
- `app/services/queue.py` — outbound message rate-limited send loop
- `app/services/campaign_enqueue.py` — contact → queue row generator
- `app/services/contact_check_worker.py` — phone checker orchestrator
- `app/services/checker.py` — actual Telethon phone resolve logic
- `app/services/warmup.py` — inter-account AI warmup worker
- `app/services/kb_ingest_worker.py` — document → chunk → embed pipeline

**AI layer:**
- `app/services/ai_engine.py` — OpenAI chat wrapper, built-in tools, function dispatch
- `app/services/kb_search.py` — pgvector search, called from ai_engine tool
- `app/services/kb_ingest.py` — text extract + chunk + embed (used by worker)

**Migrations:**
- `migrations/_schema_migrations.sql` — bootstrap tracking table (always runs first)
- `migrations/NNN_short_name.sql` — numbered migrations applied in lexical order

**Tests:**
- `tests/conftest.py` — test DB setup, drop-guard (`RuntimeError` if no test overlay)
- `tests/utils/openai_mocks.py` — shared OpenAI stubs
- `docker-compose.test.yml` — ephemeral `db-test` (tmpfs), overrides DATABASE_URL

**Frontend API client:**
- `src/lib/api.ts` — wraps all backend calls with Supabase JWT auth header
- `src/lib/supabase.ts` — Supabase JS client (browser-only, SSR stub)

**Frontend routes (all behind `_authenticated` guard):**
- `src/routes/_authenticated/campaigns.$id.tsx` — campaign detail with pool health
- `src/routes/_authenticated/inbox.tsx` — conversation inbox
- `src/routes/_authenticated/accounts.tsx` — sender management + onboarding

---

## Naming Conventions

**Backend files:**
- Routers: `app/routers/{feature}.py` — snake_case, one router per domain
- Services: `app/services/{service_name}.py` — snake_case
- Workers: class named `{Name}Worker` (e.g. `QueueWorker`, `CampaignEnqueueWorker`) with `start()`/`stop()` methods; module-level singleton instance

**Migrations:**
- Format: `NNN_short_name.sql` where NNN is zero-padded 3-digit sequence (e.g. `042_kb_id_server_defaults.sql`)
- Bootstrap exception: `_schema_migrations.sql` (underscore prefix sorts first, never tracked in the table)
- Must be idempotent: `IF NOT EXISTS`, `DO $$ EXCEPTION duplicate_object $$`, `ON CONFLICT DO NOTHING`

**ORM models:**
- All in `app/models/__init__.py`
- Class names: PascalCase (e.g. `CampaignSender`, `WarmupSession`, `KbChunk`)
- Enums: Python `enum.Enum` subclasses, `SQLEnum` for DB storage

**Frontend files:**
- Routes: file path = URL path (`campaigns.$id.tsx` → `/campaigns/:id`)
- Components: PascalCase (`EditCampaignModal.tsx`)
- Utilities: camelCase (`api.ts`, `supabase.ts`)

---

## Where to Add New Code

**New API endpoint:**
1. Add router file `app/routers/{feature}.py` with `APIRouter(prefix="/api/v1/{feature}", tags=[...])`
2. Import and register in `app/main.py` via `app.include_router(routers.{feature}.router)`
3. Add Pydantic schemas to `app/schemas/__init__.py` (or new `app/schemas/{feature}.py`)
4. All endpoints must use `Depends(auth_dep)` and scope queries with `.where(Model.workspace_id == ctx.workspace_id)`

**New ORM model:**
1. Add class to `app/models/__init__.py` (inherits `Base`)
2. Use `server_default=text("gen_random_uuid()")` on UUID PKs for raw-INSERT compatibility
3. Add a migration `migrations/NNN_short_name.sql` if `create_all` won't fully cover it (indexes, FK constraints, enum values, etc.)
4. Migration MUST be idempotent

**New migration:**
1. Create `migrations/NNN_short_name.sql` (NNN = next number in sequence)
2. Use `IF NOT EXISTS` / `DO $$ EXCEPTION duplicate_object $$`
3. Rebuild API: `docker compose up -d --build api` — applier picks it up on startup

**New background worker:**
1. Create `app/services/{worker_name}.py` with a class following `QueueWorker`/`CampaignEnqueueWorker` pattern
2. Add `start()`/`stop()` methods; `stop()` should cancel the asyncio task and await it
3. Wire in `app/main.py` lifespan: call `worker.start()` in startup block, `await worker.stop()` in shutdown block

**New frontend screen:**
1. Create `src/routes/_authenticated/{screen}.tsx` — TanStack Router auto-discovers it
2. Add API calls via `src/lib/api.ts` wrapper (not raw `fetch`)
3. Add route link to `src/components/AppSidebar.tsx`

**Shared utilities:**
- Phone/identity helpers: `app/utils/phone.py`
- Auth helpers: `app/utils/auth.py` (add only cross-cutting auth primitives)

---

## Special Directories

**`lovable-handoff/` (backend):**
- Purpose: OpenAPI spec + screen contracts consumed by Lovable during frontend generation
- Generated: Partially (openapi.json is maintained by hand/tooling)
- Committed: Yes — serves as the frontend ↔ backend contract

**`migrations/`:**
- Generated: No — hand-written SQL
- Committed: Yes — required in Docker image (auto-applier reads from filesystem)

**`dist/` (frontend):**
- Generated: Yes — `bun run build` output
- Committed: Yes (currently) — Cloudflare Pages deploy artifact

**`tests/__pycache__/`, `app/**/__pycache__/`:**
- Generated: Yes
- Committed: No (gitignored)

**`.planning/`:**
- Purpose: GSD planning documents (phases, roadmap, codebase maps)
- Committed: Yes (to backend repo only)

---

*Structure analysis: 2026-06-30*
