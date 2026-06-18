# Coding Conventions

**Analysis Date:** 2026-06-18

---

## Backend (Python)

### Naming Patterns

**Files:**
- Modules use `snake_case`: `ai_engine.py`, `queue.py`, `campaign_enqueue.py`
- Routers mirror the resource name: `campaigns.py`, `conversations.py`, `senders.py`
- Tests prefixed `test_`: `test_campaign_router.py`, `test_phase5_1_core_value_e2e.py`
- Migration files: `NNN_short_name.sql` (e.g. `016_phase4.sql`, `026_campaign_allow_recontact.sql`)

**Functions:**
- Public service functions: `snake_case` — `enqueue_message`, `get_context`, `notify_signal`
- Private helpers: leading underscore — `_make_campaign`, `_load_conversation_or_404`, `_campaign_in_working_window`
- Async functions for all DB/network I/O — no sync variants

**Classes:**
- ORM models: `PascalCase` — `Workspace`, `CampaignSender`, `WorkspaceApiKey`
- Pydantic schemas: `PascalCase` with `Request`/`Response` suffix — `CampaignCreate`, `CampaignListResponse`, `SendMessageFromUIRequest`
- Enum classes: `PascalCase` — `QueueItemStatus`, `MessageType`
- Service singletons: `snake_case` instance at module level — `ai_engine`, `telegram_service`, `queue_worker`

**Variables:**
- `snake_case` throughout
- Constants: `UPPER_SNAKE_CASE` — `MIN_SEND_INTERVAL`, `BUILT_IN_TOOL_NAMES`, `MAX_ATTEMPTS`

### Code Style

**Formatting:**
- No formatter config present (no Black/isort config); style is consistent but not tooling-enforced
- Module-level docstrings are standard — every `app/` file begins with a triple-quoted docstring describing phase, endpoints, and constraints
- Import groups: stdlib → third-party → internal `app.*`, each separated by blank line

**Type annotations:**
- Function signatures consistently annotated: `async def _load_conversation_or_404(db: AsyncSession, ctx: AuthCtx, conversation_id: UUID) -> dict:`
- `Optional[str]` used (not `str | None`) in most existing code; `str | None` appears in newer code
- `from __future__ import annotations` used selectively in newer test files

### Import Organization

**Order (per module):**
1. `from __future__ import annotations` (when present)
2. Standard library (`asyncio`, `logging`, `uuid`, `datetime`, etc.)
3. Third-party (`fastapi`, `sqlalchemy`, `pydantic`, `httpx`, `telethon`)
4. Internal `app.*` (`app.config`, `app.database`, `app.models`, `app.schemas`, `app.services.*`, `app.utils.*`)

**Deferred imports in tests:**
- Service-under-test imported inside the test function body to avoid circular or premature module load: `from app.services.ai_engine import ai_engine`
- ORM models imported at conftest module level only after env vars are set (see `tests/conftest.py`)

### Error Handling

**Router pattern — structured detail dict:**
```python
raise HTTPException(
    status_code=404,
    detail={"code": "CONVERSATION_NOT_FOUND",
            "message": "Conversation not found"},
)
```
- Error codes are `UPPER_SNAKE_CASE` strings matching `docs/error-codes.md` on the frontend
- Cross-workspace resource access returns 404 (not 403) — "cross-workspace = 404, not 403"
- Global exception handlers in `app/main.py` add CORS headers to all 4xx/5xx responses so browser doesn't mask errors as "CORS blocked"

**Service layer:**
- Services raise domain exceptions or return `None` for not-found; routers translate to HTTP
- `SessionAuthError` raised by `app/services/telegram.py` and caught in routers/queue worker
- `FloodWaitError` from Telethon handled with retry logic — do not modify without explicit discussion

**Validation:**
- Pydantic `model_validator` used for cross-field validation
- `AliasChoices` used where frontend sends alternate field names (e.g. `message` vs `message_text` in `SendMessageFromUIRequest`)
- `Field(...)` required fields annotated with `description=` for OpenAPI docs

### Logging

**Framework:** stdlib `logging`

**Setup pattern (all modules):**
```python
logger = logging.getLogger(__name__)
```

**Log levels:**
- `logger.info(...)` for lifecycle events (worker start/stop, DB init)
- `logger.warning(...)` for recoverable anomalies (rate limit hit, FloodWait, invalid timezone)
- `logger.error(..., exc_info=True)` for unhandled exceptions in background workers
- `logger.exception(...)` in the global unhandled exception handler in `app/main.py`
- **Never `print()`** — forbidden by `CLAUDE.md`

### Async Rules

- **Async everywhere**: all DB interactions use `async with AsyncSession`, `await session.execute(...)`, `await session.commit()`
- `AsyncSessionLocal` used in background workers (no request context); `get_db` dependency used in routers
- **Never `time.sleep()`** — use `await asyncio.sleep(...)`
- **Never `requests`** — use `httpx.AsyncClient` or `httpx` async context managers

### Migration Conventions

- Files in `migrations/` named `NNN_short_name.sql`, applied in lexical order
- Every migration must be **idempotent**: `CREATE TABLE IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`, `DROP TABLE IF EXISTS`, `ON CONFLICT DO NOTHING`, `DO $$ … EXCEPTION duplicate_object $$`
- Tracked in `schema_migrations` table; auto-applied at api startup via `app/database.py::_apply_migrations`
- No Alembic — raw SQL only

### Comments

**When to comment:**
- Every router file has a module-level docstring listing endpoints, phase tags (e.g. `CAMP-01..04`), and key behavioral constraints
- Inline `# Phase N D-NN:` comments explain non-obvious decisions tied to a specific design decision ID
- `# TODO(v2):` tags mark deferred work — do not remove
- `# Pitfall N` comments mark known footguns documented in `AGENTS.md` / `CLAUDE.md`

### Pydantic Schemas

- All schemas in `app/schemas/__init__.py`
- `BaseModel` with `model_config = ConfigDict(from_attributes=True)` where ORM serialization needed
- Response models returned by router functions, validated by FastAPI automatically
- `Optional[X] = None` for nullable fields; `Field(default_factory=dict)` for mutable defaults

---

## Frontend (TypeScript/React)

### AGENTS.md Rules (Authoritative)

The file `/root/apps/aimly/aimly-tg-outreach/AGENTS.md` is the canonical frontend contract. These rules are enforced:

1. **No invented backend types.** Import all `/api/v1/*` request/response types from `@/types/api.ts` (auto-generated from backend OpenAPI via `openapi-typescript`). If a type is missing, flag it — do not guess.
2. **All forms use `react-hook-form` + `zod`.** Zod schemas live in `src/lib/validators/*.ts`.
3. **All data fetching uses TanStack Query.** Cache keys pattern: `['<resource>', ...params]`. Use `refetchInterval` only where UI-SPEC §5 specifies (inbox = 10s, dashboard = 30s).
4. **Design tokens come from `src/styles/aimly.css`.** Do not redefine CSS variables inline.
5. **No new motion libraries.** CSS keyframes only. Always wrap in `@media (prefers-reduced-motion: reduce) { animation: none }`.
6. **Rate limits 4/20/150 are not configurable in v1** — never offer a UI control for them (hard backend constraint).
7. **AI accent `--ai-purple #8774e1`** reserved for `<live-dot>`, `<ai-shimmer>`, thought-trace, AI co-pilot panel, launch overlay, AI suggestion chips only.
8. **Brand "aimly" is always lowercase**, no exclamation marks.
9. **No `console.log` in committed code.** Use `import.meta.env.DEV` guards if needed.
10. **All telemetry through `track(event, props)` from `src/lib/telemetry.ts`** — POST to `/api/v1/telemetry/events`. Use `navigator.sendBeacon` on `pagehide`.

### Naming Patterns

**Files:**
- Components: `PascalCase.tsx` — `AppSidebar.tsx`, `EditCampaignModal.tsx`, `OnboardingFlow.tsx`
- Route files: dot-separated segments — `campaigns.index.tsx`, `campaigns.$id.tsx`, `campaigns.new.tsx`
- Lib modules: `kebab-case.ts` — `api.ts`, `error-codes.ts`, `error-capture.ts`, `supabase.ts`, `telemetry.ts`
- shadcn/ui primitives: `kebab-case.tsx` in `src/components/ui/`

**Functions and variables:**
- Components: `PascalCase` function declarations — `function AppSidebar(...)`, `function CampaignsPage()`
- Hooks: `camelCase` with `use` prefix — `useMobile`, `useQuery`, `useMutation`
- Constants: `UPPER_SNAKE_CASE` for static lookup data — `STATUS_PILL`, `AVATAR_COLORS`, `TABS`
- Local helpers: `camelCase` — `avatarStyle`, `errMsg`, `buildUrl`

**Types:**
- Interface names: `PascalCase` — `NavItem`, `Props`, `ApiOptions`
- Generated OpenAPI types consumed via: `type Campaign = components["schemas"]["CampaignResponse"]`
- All backend request/response shapes imported from `@/types/api.ts` only

### Code Style

**Prettier config at `/root/apps/aimly/aimly-tg-outreach/.prettierrc`:**
- `printWidth: 100`
- `semi: true`
- `singleQuote: false` — double quotes everywhere
- `trailingComma: "all"`

**ESLint config at `/root/apps/aimly/aimly-tg-outreach/eslint.config.js`:**
- `typescript-eslint` recommended rules
- `eslint-plugin-react-hooks` recommended rules
- `eslint-plugin-react-refresh` — warns on non-component exports from route files
- `eslint-plugin-prettier` — formatting enforced as lint errors
- `@typescript-eslint/no-unused-vars` is **off** (intentional — Lovable-generated files have unused imports)
- `no-restricted-imports` blocks the `server-only` package (use `*.server.ts` convention instead)

### Import Organization

**Path aliases:** `@/` maps to `src/` (via `vite-tsconfig-paths`)

**Order:**
1. Framework imports (`react`, `@tanstack/react-router`, `@tanstack/react-query`)
2. lucide-react icons
3. Internal `@/components/...`
4. Internal `@/lib/...`
5. Internal `@/types/...`

### API Call Pattern

All HTTP calls go through `api<T>(path, opts)` from `src/lib/api.ts`:

```typescript
const data = await api<CampaignList>("/api/v1/campaigns", { method: "GET" });
```

- Returns typed `T`, throws `ApiError` on non-2xx
- `ApiError` carries `.status`, `.code`, `.message`, `.detail`
- Error codes mapped to user-facing strings via `src/lib/error-codes.ts`
- 401 + `TOKEN_EXPIRED` dispatches `aimly:auth-expired` custom event for global sign-out

### Error Handling (Frontend)

**Standard component error helper:**
```typescript
function errMsg(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return "Something went wrong";
}
```

**Per AGENTS.md requirements:**
- 401 redirects to `/login` with toast "Your session expired. Sign in again."
- Error envelope `{code, message}` mapped via `src/lib/error-codes.ts`
- Empty states use 4-element formula: icon + heading + body + CTA

### Routing

**Framework:** TanStack Router (file-based routing)
- Route files in `src/routes/`
- Authenticated subtree: `src/routes/_authenticated.tsx` — `ssr: false`, Supabase session guard in `beforeLoad`
- Dev bypass in auth guard: `if (import.meta.env.DEV) return` (for Lovable preview)
- Route tree auto-generated into `src/routeTree.gen.ts` — do not edit manually

### Telemetry

All product events fired via `track(event, props)` from `src/lib/telemetry.ts`. Events are batched and flushed with a 1.5s debounce; `navigator.sendBeacon` is used on `pagehide`. The `TelemetryEvent` union type in `src/lib/telemetry.ts` is the authoritative frontend event list — it must stay in sync with the backend whitelist in `app/routers/telemetry.py::_EVENT_WHITELIST`.

### Authentication

- Supabase magic link auth — `src/lib/supabase.ts`
- JWT passed as `Authorization: Bearer <token>` on every API call by the `api()` helper
- Token retrieved via `supabase.auth.getSession()` with 20-retry wait loop (handles auth state hydration lag)
- Backend verifies via ES256 JWKS (primary) or HS256 secret (legacy fallback) — `app/utils/auth.py`
- Supabase JWT algorithm must be pinned to HS256 in Supabase Dashboard for legacy path; ES256 is default for new projects

### Accessibility Requirements (per AGENTS.md)

Before a screen is considered done:
- Lighthouse accessibility >= 90
- Reduced-motion CSS guard present on all animations
- All icon-only buttons have `aria-label`

---

*Convention analysis: 2026-06-18*
