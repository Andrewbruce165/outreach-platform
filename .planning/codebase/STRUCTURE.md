# Codebase Structure

**Analysis Date:** 2026-07-09

## Directory Layout

```
tg-outreach/                          # backend repo root (Andrewbruce165/outreach-platform)
├── app/                               # FastAPI application (the backend)
│   ├── main.py                        # FastAPI app, lifespan, CORS, error handlers
│   ├── config.py                      # pydantic-settings Settings (.env-backed)
│   ├── database.py                    # engine, AsyncSessionLocal, migration applier
│   ├── data/                          # static data files bundled with the app
│   │   └── control_set_known_live.txt # checker health-probe control numbers
│   ├── routers/                       # one module per REST resource (18 files)
│   ├── services/                      # business logic + background workers (30 files)
│   │   └── llm/                       # LLM provider abstraction (OpenAI/Anthropic)
│   ├── models/
│   │   └── __init__.py                # ALL ORM models in one file (~1080 lines, ~37 classes)
│   ├── schemas/
│   │   ├── __init__.py                # most Pydantic schemas (~1360 lines)
│   │   └── knowledge_bases.py         # KB-specific schemas (split out, Phase 16)
│   └── utils/                         # auth.py, phone.py, names.py, location.py
├── migrations/                        # raw SQL, NNN_short_name.sql, auto-applied at boot
│   └── _schema_migrations.sql         # bootstrap (tracking table itself)
├── tests/                             # flat pytest suite, ~137 test_*.py files (no subpackages except utils/)
│   ├── conftest.py                    # DB fixture setup + prod-safety guard
│   └── utils/
├── scripts/                           # one-off ops/maintenance Python scripts (not part of the app)
├── docs/                              # misc backend docs (db schema notes, etc.)
├── lovable-handoff/                   # generated contract handoff for the Lovable frontend tool
│   ├── openapi.json                   # canonical API contract fed to Lovable
│   ├── types/api.ts                   # generated TS types mirror
│   ├── design-source/                 # Lovable project/chat exports
│   └── *.md                           # PRD, screen-build-order, error-codes, telemetry-events, KNOWLEDGE
├── frontend/                          # VENDORED frontend app (added 2026-07-09, commit c176901)
│   ├── src/
│   │   ├── routes/                    # TanStack Router file-based routes
│   │   │   ├── __root.tsx
│   │   │   ├── login.tsx / auth.callback.tsx
│   │   │   └── _authenticated/        # layout-gated routes (Supabase session required)
│   │   ├── components/                # feature components (StageEditor, AppSidebar, ...)
│   │   │   └── ui/                    # shadcn primitives
│   │   ├── lib/                       # api.ts, supabase.ts, telemetry.ts, error-codes.ts, utils.ts
│   │   ├── hooks/                     # use-mobile.tsx (minimal — most state is per-route)
│   │   ├── types/                     # frontend-local TS types
│   │   └── styles/
│   ├── design-source/, docs/, .lovable/  # Lovable tooling metadata (mirrors lovable-handoff/)
│   ├── dist/                          # build output (client/ = static SPA, server/ = inert SSR bundle)
│   ├── vite.config.ts                 # spa:{enabled:true}, nitro:false (static SPA, no Workers deploy)
│   ├── wrangler.jsonc                 # legacy Cloudflare Workers config (unused in current deploy path)
│   └── package.json                   # bun-based scripts (dev/build/lint/format)
├── .planning/                         # GSD planning artifacts (phases, debug logs, notes, reviews)
├── docker-compose.yml                 # services: db (pgvector/pg16), api, listener
├── docker-compose.test.yml            # test overlay: ephemeral db-test in tmpfs
├── Dockerfile / Dockerfile.listener    # api image / listener image
├── deploy-frontend.sh                 # docker-bun build + rsync dist/client -> /var/www/aimly
├── backup.sh                          # pg_dump cron script
└── requirements.txt / pyproject.toml  # backend deps (no lockfile — pip, not poetry/uv locked)
```

**Deploy topology note:** the frontend is NOT run as a container/process in production. `deploy-frontend.sh` builds it in an ephemeral `oven/bun:1` container and `rsync`s `dist/client/` to `/var/www/aimly/` on the host; nginx (`/etc/nginx/sites-available/aimly.agsventurelab.com`) serves those static files directly and reverse-proxies `/api/` to `127.0.0.1:8005` (the `api` container). `frontend/dist/` in the repo is build output, not source.

## Directory Purposes

**`app/routers/`:**
- Purpose: HTTP endpoint definitions, one file per resource domain.
- Contains: `APIRouter()` instances with `prefix="/api/v1/<resource>"`, Pydantic request/response wiring, `Depends(auth_dep)`.
- Key files: `campaigns.py` (campaign CRUD + lifecycle, largest/most active router), `senders.py`, `warmup.py`, `account_import.py` (Phase 21 bulk import), `knowledge_bases.py` (Phase 16 RAG), `grade_settings.py` (Phase 22), `check_contacts.py`, `conversations.py`, `telemetry.py` (has an event whitelist that must be updated when the frontend adds new tracked events).

**`app/services/`:**
- Purpose: all business logic and every long-running background worker.
- Contains: Telethon wrapper (`telegram.py`), queue engine (`queue.py`), listener (`listener.py` — runs in its own container, not imported by the API's own lifespan), AI reply engine (`ai_engine.py`), checker/contact-resolution (`checker.py`, `contact_check_worker.py`), warmup (`warmup.py`), campaign enqueue (`campaign_enqueue.py`), KB ingestion (`kb_ingest.py`, `kb_ingest_worker.py`, `kb_search.py`), grade ladder (`grade_ladder.py`, `grade_progression.py`), restriction audit (`restriction_audit.py`), encryption (`encryption.py`), CSV/account import (`csv_import.py`, `account_import.py`, `account_import_worker.py`).
- Key files: `services/llm/resolve.py` (chooses OpenAI vs Anthropic per workspace `LLMSettings`) — go here first when adding a new LLM provider.

**`app/models/`:**
- Purpose: single source of truth for the DB schema at the ORM level.
- Contains: all `Base`-derived classes plus shared enums (`MessageType`, `QueueItemStatus`, `QueueItemType`) in one `__init__.py`. There is intentionally no per-model file split — grep `^class ` in this file to find a model.

**`app/schemas/`:**
- Purpose: Pydantic I/O contracts for the API.
- Contains: mostly in `__init__.py`; `knowledge_bases.py` is the only model split into its own file. Note some routers (e.g. `campaigns.py`) define small local `BaseModel`s inline instead of adding to `schemas` — check both when tracing a contract.

**`migrations/`:**
- Purpose: append-only raw SQL, auto-applied at API boot (`app/database.py::_apply_migrations`), tracked in `schema_migrations`.
- Contains: `NNN_short_name.sql`, 3-digit lexically-sortable prefix, up to `060_campaign_attachments_multiple.sql` as of 2026-07-09. Every file must be idempotent (`IF NOT EXISTS`, `DO $$ ... EXCEPTION duplicate_object $$`, `ON CONFLICT DO NOTHING`) because the applier re-runs unrecorded files on every start, including a from-scratch DB.

**`tests/`:**
- Purpose: pytest suite, ~137 `test_*.py` files, essentially flat (one subpackage `tests/utils/`).
- Contains: `conftest.py` holds the DB fixture setup and the **production-safety guard** that raises `RuntimeError` unless `DATABASE_URL` points at the test overlay — never run pytest without `docker-compose.test.yml`.

**`frontend/`:**
- Purpose: the SPA UI, vendored into this repo's history as of 2026-07-09 (previously developed only in the sibling repo `AGS-Venture-Lab/aimly-tg-outreach`, still clonable standalone at `/root/apps/aimly/aimly-tg-outreach`).
- Contains: TanStack Start app in static-SPA mode (`nitro:false`, `spa.enabled:true`) — SSR code exists but is never served. React + TypeScript + Vite + bun + shadcn/Radix + Tailwind.
- Key files: `frontend/src/lib/api.ts` (typed fetch client, resolves Supabase access token, `VITE_BACKEND_URL` base), `frontend/src/lib/supabase.ts` (auth client), `frontend/vite.config.ts` (deploy-mode config, heavily commented — do not manually add plugins already provided by `@lovable.dev/vite-tanstack-config`).

**`lovable-handoff/`:**
- Purpose: the contract package fed to the Lovable code-generation tool that (historically) produced/updates the frontend.
- Contains: `openapi.json` (canonical, hand/generator-maintained API spec — source of truth when frontend and backend drift), `types/api.ts`, PRD and screen-build-order docs, `error-codes.md`, `telemetry-events.md`. Near-duplicates of some of these live under `frontend/docs/` — treat `lovable-handoff/` as canonical and `frontend/docs/` as the copy Lovable pulled in.

**`.planning/`:**
- Purpose: GSD (this tool's) planning artifacts — phases, debug write-ups, notes, reviews. Not application code.

## Key File Locations

**Entry Points:**
- `app/main.py`: FastAPI app + lifespan (starts/stops all 8 background workers) — served via `uvicorn app.main:app`.
- `app/services/listener.py`: Telegram listener entry, run via `python -m app.services.listener` in its own container.
- `frontend/src/routes/__root.tsx`: SPA root route/shell.

**Configuration:**
- `app/config.py`: `Settings(BaseSettings)` — reads `.env`, includes `database_url`, `telegram_api_id/hash`, `encryption_key`, `supabase_url`/`supabase_jwt_secret`, CORS origins/regex, Decodo proxy pool vars.
- `docker-compose.yml` / `docker-compose.test.yml`: service topology and env wiring for db/api/listener; test overlay swaps in an ephemeral tmpfs Postgres.
- `frontend/vite.config.ts`, `frontend/wrangler.jsonc` (legacy Cloudflare config, superseded by static-SPA + nginx deploy), `frontend/.env` (exists — contents not read, see forbidden files).

**Core Logic:**
- `app/services/queue.py`: rate-limited send engine (do not touch tuned constants without discussion — see project CLAUDE.md).
- `app/services/telegram.py`: Telethon client wrapper shared by API and listener.
- `app/services/ai_engine.py`: AI reply generation + RAG + typing-hold.
- `app/utils/auth.py`: dual-auth dependency (`auth_dep`) used by every workspace-scoped router.
- `app/database.py`: engine/session + migration applier.

**Testing:**
- `tests/conftest.py`: fixtures + prod-safety guard.
- `tests/test_*.py`: one file per feature/service, matches `app/services/*.py` and `app/routers/*.py` naming closely (e.g. `test_checker*.py` cluster covers `app/services/checker.py` + `contact_check_worker.py`).

## Naming Conventions

**Files:**
- Backend: `snake_case.py`, one file per router/service, named after the resource/domain it owns (`campaign_enqueue.py`, `grade_progression.py`). Test files mirror the module under test: `test_<module_or_feature>.py`.
- Migrations: `NNN_short_name.sql` — 3-digit zero-padded sequence prefix, short lowercase snake description.
- Frontend routes: TanStack Router file-based convention — `.` in filename creates nested/param segments (`campaigns.$id.tsx` = `/campaigns/:id`, `campaigns.index.tsx` = `/campaigns` index, `_authenticated.tsx` = pathless layout route wrapping the `_authenticated/` directory).
- Frontend components: `PascalCase.tsx` for feature components (`AppSidebar.tsx`, `EditCampaignModal.tsx`); shadcn primitives under `components/ui/` follow shadcn's own lowercase-kebab convention.

**Directories:**
- Backend: flat-by-layer (`routers/`, `services/`, `models/`, `schemas/`, `utils/`), not flat-by-feature — a single feature (e.g. campaigns) spans `routers/campaigns.py` + relevant classes inside `models/__init__.py` + `schemas/__init__.py` + one or more `services/*.py`.
- Frontend: flat-by-layer under `src/` (`routes/`, `components/`, `lib/`, `hooks/`, `types/`), with `_authenticated/` as the one layout-based grouping.

## Where to Add New Code

**New backend REST resource:**
- Router: new `app/routers/<resource>.py`, register in `app/main.py` imports + `app.include_router(...)` (grep `app.include_router` near the bottom of `main.py`).
- Models: add class(es) to `app/models/__init__.py` (no new file).
- Schemas: add to `app/schemas/__init__.py`, or a new `app/schemas/<resource>.py` if it's a large/self-contained domain (precedent: `knowledge_bases.py`).
- Migration: new `migrations/0NN_<name>.sql`, idempotent.
- Tests: new `tests/test_<resource>.py`.

**New background worker:**
- Add `app/services/<name>_worker.py` (or a `*_worker` object inside an existing service file if small), implement `.start()`/`.stop()` following the existing singleton pattern (see `campaign_enqueue.py` or `follow_up.py` for a recent example).
- Wire into `app/main.py` lifespan: import, `<worker>.start()` in startup block, `await <worker>.stop()` in shutdown block (reverse order of start).

**New LLM provider:**
- Add `app/services/llm/<provider>_provider.py` implementing the interface in `base.py`, register in `resolve.py`'s provider selection logic, extend `capabilities.py`/`models_filter.py` if the provider needs feature gating.

**New frontend page:**
- Add a route file under `frontend/src/routes/_authenticated/` (or top-level `frontend/src/routes/` if unauthenticated) following the TanStack file-based naming above.
- Feature-specific components go in `frontend/src/components/` (PascalCase); reusable primitives should be pulled from `components/ui` (shadcn) rather than hand-rolled.
- API calls go through `frontend/src/lib/api.ts`'s `api<T>()` helper, not raw `fetch`.

**Utilities:**
- Backend shared helpers: `app/utils/` (`auth.py`, `phone.py`, `names.py`, `location.py`) — add a new file here for cross-router/cross-service pure functions.
- Frontend shared helpers: `frontend/src/lib/utils.ts` (generic) or a new file in `frontend/src/lib/` for a new cross-cutting concern (mirrors `error-codes.ts`, `telemetry.ts`, `error-capture.ts`, `error-page.ts`).

## Special Directories

**`app/data/`:**
- Purpose: static data bundled with the app (currently one file: `control_set_known_live.txt`, the checker health-probe control set of known-live numbers).
- Generated: No (hand-curated).
- Committed: Yes.

**`frontend/dist/`:**
- Purpose: build output (`dist/client/` = deployable static SPA; `dist/server/` = inert SSR bundle, never served).
- Generated: Yes, by `bun run build`.
- Committed: check `.gitignore` before assuming — present on disk currently but is build output and should generally not be treated as source of truth.

**`frontend/node_modules/`, `frontend/bun.lock`:**
- Purpose: JS dependency tree / lockfile.
- Generated: `node_modules/` yes; `bun.lock` is the committed lockfile (source of truth for reproducible installs, used by `deploy-frontend.sh`'s `--frozen-lockfile`).

**`lovable-handoff/` vs `frontend/docs/` vs `frontend/design-source/` / `frontend/.lovable/`:**
- Purpose: `lovable-handoff/` (repo root) is this backend repo's canonical contract package pushed *to* Lovable. `frontend/docs/`, `frontend/design-source/`, `frontend/.lovable/project.json` are artifacts Lovable pulled/generated *inside* the frontend project — largely mirrors, not independently maintained. When the two disagree, treat `lovable-handoff/openapi.json` (backend-authored) as ground truth for the API contract.
- Generated: Lovable-tooling-managed.
- Committed: Yes (both sides).

**`.planning/`:**
- Purpose: GSD planning documents (phases, debug logs, notes, reviews, quick-task branches). Not consumed by the running application.
- Generated: by GSD commands.
- Committed: Yes.

**`scripts/`:**
- Purpose: one-off/ad-hoc ops scripts (e.g. `bulk_clear_polina_lastnames.py`, `bulk_resync_profiles.py`) run manually against prod data — not imported by the app, no test coverage expected.
- Generated: No.
- Committed: Yes.

---

*Structure analysis: 2026-07-09*
