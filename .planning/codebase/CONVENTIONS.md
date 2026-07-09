# Coding Conventions

**Analysis Date:** 2026-07-09

## Scope note

This document covers the Python backend at the repo root (`app/`, `migrations/`, `tests/`). A vendored `frontend/` directory (TanStack Start / React / TypeScript, imported into this repo's history via `Add 'frontend/' from commit ...`) also exists, but the actively-developed, Lovable-generated frontend lives in the separate sibling repo `/root/apps/aimly/aimly-tg-outreach` — see root `CLAUDE.md`. Frontend conventions are out of scope here.

## Naming Patterns

**Files:**
- `app/routers/<domain>.py` — one router module per resource (`senders.py`, `campaigns.py`, `contacts.py`, `warmup.py`, ...). Router-internal helpers are private (`_leading_underscore`) and colocated in the same file, grouped under a `# ─── Section ─── ` comment banner rather than split into separate helper modules.
- `app/services/<domain>.py` — one service module per subsystem (`queue.py`, `checker.py`, `rebalance.py`, `warmup.py`, `ai_engine.py`). Workers that run as background loops are suffixed `_worker.py` (`contact_check_worker.py`, `kb_ingest_worker.py`, `account_import_worker.py`).
- `app/models/__init__.py` — **all ORM models live in one file** (single `Base` module), not split per-model. Do not create `app/models/sender.py` etc. — add new models to this file, in the same style as existing ones (grouped under `# ─── Phase N — FEATURE ─── ` banners).
- `app/schemas/__init__.py` — likewise, **all Pydantic request/response schemas live in one file** (1359 lines), grouped by resource under `# === Resource ===` banners. `app/schemas/knowledge_bases.py` is the one exception (Phase 16 KB schemas split out) — new large feature areas MAY get their own schema file, but the default is the shared `__init__.py`.
- Migrations: `migrations/NNN_short_name.sql`, zero-padded 3-digit sequence (`012_workspace.sql` ... `060_campaign_attachments_multiple.sql`). Never reuse a number; always the next integer even across phases.
- Tests: `tests/test_<feature>.py`, often prefixed with the phase name for phase-scoped test suites (`test_phase5_1_agents_v2.py`, `test_phase5_analytics.py`). Debug/one-off investigation docs go in `.planning/debug/`, not test files.

**Functions:**
- `snake_case` throughout, always `async def` for anything touching DB/network/Telegram/OpenAI (see Architectural rule "Async everywhere" in root CLAUDE.md).
- Private/internal helpers prefixed `_` (`_derive_status`, `_load_sender_by_slug`, `_check_profile_cooldown`). Router files freely define several `_`-prefixed helpers above the route handlers.
- Route handler function names mirror the REST verb + resource: `list_senders`, `create_sender`, `get_sender`, `update_sender`, `delete_sender`, `pause_sender`, `resume_sender`.

**Variables:**
- `snake_case`. Short DB-row aliases (`s`, `cr`, `wid`, `cid`, `sid`) are common inside SQL-building helpers where the field is obvious from a nearby `text("""...""")` block, but any variable that crosses a function boundary or appears in a docstring reference uses a full name (`sender_id`, `workspace_id`).
- IDs are consistently `UUID` (Python `uuid.UUID`) at the ORM/schema layer, stringified (`str(x)`) only when binding into raw SQL params (`{"sid": str(sender.id)}`) — asyncpg/SQLAlchemy `text()` params need strings, not UUID objects, when the query isn't going through the ORM.

**Types / Classes:**
- `PascalCase` for ORM models (`Sender`, `Workspace`, `AIContext`, `SenderRestrictionEvent`) and Pydantic schemas (`SenderCreate`, `SenderResponse`, `SenderCreateResponse`). Response/request pairs follow `<Resource><Verb>` or `<Resource><Verb>Response` (`ProfileUpdate` / `ProfileUpdateResponse`, `GradeOverrideRequest`).
- Enums: Python `enum.Enum` classes for a handful of DB-backed enums (`MessageType`, `QueueItemStatus`, `QueueItemType` in `app/models/__init__.py`), but **most status/lifecycle fields are plain `String` columns with a Postgres `CHECK` constraint** (e.g. `restriction_status IN ('none','spam_limited','frozen')`), not a mapped Python enum. When adding a new status field, check whether the existing pattern for that table is enum-backed or CHECK-constrained and follow it — don't introduce a third style.
- `AuthCtx` (in `app/utils/auth.py`) is the canonical `pydantic.BaseModel` used as a FastAPI dependency return type — follow this pattern (a typed `BaseModel`, not a bare dict/tuple) for any new cross-cutting request context.

## Code Style

**Formatting:**
- No `black`/`ruff`/`flake8` config exists in the repo (`pyproject.toml` only has `[tool.pytest.ini_options]`). There is no enforced auto-formatter — match the surrounding file's line width (~88-100 cols observed) and spacing by hand.
- No `.eslintrc`/`.prettierrc` in the backend (those apply only inside the separate frontend repo).

**Docstrings:**
- Every router, service module, non-trivial function, and most fixtures carry a `"""triple-quoted docstring"""` — this codebase leans heavily on docstrings-as-decision-log. A typical docstring documents:
  1. What the function/endpoint does (one line).
  2. **Why** — cross-references to phase/decision IDs (`D-14`, `RESV-01`, `POOL-09`), dates, or a named bug/incident (`2026-05-26 hotfix`, `Phase 22 D-04`).
  3. Edge cases / gotchas a future reader would otherwise rediscover the hard way.
- New code MUST follow this pattern: **when a piece of logic exists because of a specific bug, incident, or design decision, say so in the docstring/comment with a dated or D-NN reference**, not just "handles edge case X". This repo's docstrings are effectively inline ADRs — the codebase depends on them for context since there's no ticket tracker.
- Comments are bilingual: prose docstrings often mix Russian explanation with English code-identifiers; inline `# comment` lines are freely either language depending on the author's original PR. Follow whichever language dominates the file you're editing — don't force-translate existing comments.

## Import Organization

**Order (observed, not enforced by tooling):**
1. stdlib (`import logging`, `from datetime import ...`, `from uuid import UUID`)
2. third-party (`from fastapi import ...`, `from sqlalchemy import ...`)
3. `app.*` absolute imports (`from app.database import get_db`, `from app.models import Sender`, `from app.schemas import ...`, `from app.services...`, `from app.utils.auth import AuthCtx, auth_dep`)

**Deferred/local imports are a deliberate, common pattern** — many routers do `from app.services.telegram import telegram_service, SessionAuthError` *inside* the route function body rather than at module top. Reasons observed in comments: avoid heavy/optional dependency cost at collection time, avoid circular imports between `services` and `routers`, or keep a not-yet-existing helper out of `--collect-only` during red/TDD scaffolding (see `tests/test_checker_probe.py`). When adding a router endpoint that needs a service with heavy import cost or a circular-import risk, prefer a local import inside the function over a top-level one — this matches the existing style and is not considered a smell here.

**Path aliases:** none — always absolute `app.` imports, never relative (`from .models import ...`). No `tsconfig`-style path aliases apply to the Python side.

## Error Handling

**HTTP layer (routers):** every error path raises `fastapi.HTTPException` with a **structured `detail` dict**, never a bare string:
```python
raise HTTPException(
    status_code=404,
    detail={"code": "SENDER_NOT_FOUND", "message": f"Sender '{slug}' not found"},
)
```
- `detail["code"]` is a SCREAMING_SNAKE_CASE machine-readable error code (`RATE_LIMIT_EXCEEDS_HARD_CAP`, `TOO_FREQUENT`, `SLUG_EXISTS`, `AUTH_REQUIRED`, `TOKEN_EXPIRED`) — this is the frontend's contract for branching UI behavior, not just a human message.
- `detail["message"]` is a human-readable string, frequently **in Russian** when it's user-facing UI copy (error toasts), and English when it's an internal/API-consumer-facing message (n8n integration errors).
- Extra structured fields are added ad hoc when useful to the caller (`retry_after`, `campaigns: [...]`, `field`, `value`, `hard_cap`).
- 404 is used deliberately over 403 to avoid leaking cross-tenant existence ("не раскрываем cross-tenant существование" — see `_load_sender_by_slug`, `assign_proxy`). Follow this: a workspace-scoped lookup that misses (wrong tenant OR truly absent) always 404s, never 403.
- Third-party (Telethon/Telegram) exceptions are translated into the app's structured-error vocabulary at the boundary, not leaked raw. See `app/routers/senders.py::_raise_profile_telegram_error` — a lookup table matching on `f"{type(e).__name__} {e}".upper()` substrings maps Telegram error names (`USERNAME_OCCUPIED`, `FLOOD_WAIT`, `PASSWORD_HASH_INVALID`, ...) to `(status_code, code, ru_message)` tuples. When integrating a new Telethon RPC, extend this kind of table rather than letting the raw Telethon exception surface to the HTTP client.
- Known custom exceptions (`SessionAuthError`, `ProfileChangeRejectedError`) are defined in `app/services/telegram.py` and caught explicitly before the generic `except Exception` catch-all. Always re-raise `HTTPException` as-is if caught inside a broader `except Exception` (`except HTTPException: raise` then `except Exception as e: ...`) — never let a generic handler swallow/rewrap an already-structured HTTPException.

**Service layer:** services mostly let exceptions propagate to the caller (router) or to the worker loop's own try/except, rather than raising HTTPException themselves (HTTPException is an HTTP-layer concept). Domain-specific exceptions are custom classes (`SessionAuthError`, `ProfileChangeRejectedError`) raised from `app/services/telegram.py`.

**Logging:** `logging.getLogger(__name__)` at module top in every service/router that logs. Log messages use a `[module-tag]` prefix convention for grep-ability: `logger.info(f"[senders] created workspace={ctx.workspace_id} slug={sender.slug} ...")`, `logger.info(f"[proxy-pool] auto-assigned port ...")`, `logger.info(f"[auth] resolved existing workspace=...")`. Follow this `[tag] key=value key=value` style for new log lines — it's how logs are grepped in production (`docker logs`).
- Never `print()` — logging only (root CLAUDE.md rule, consistently followed in the code read).
- Never `time.sleep()` — worker loops use `await asyncio.sleep(...)`.

## Function Design

**Size:** router handlers and service functions are often long (50-150 lines) because business rules/edge cases are inlined with heavy commenting rather than split into many tiny private functions — the codebase favors "one function you can read top-to-bottom with its full context" over deep decomposition. Helper functions (`_derive_status`, `_check_profile_cooldown`, `_validate_rate_limits`) are extracted only when the same logic is reused across multiple endpoints, not purely for line-count reasons.

**Parameters:** FastAPI dependency injection is idiomatic and pervasive — every DB-backed route takes `ctx: AuthCtx = Depends(auth_dep)` (workspace/auth context) and `db: AsyncSession = Depends(get_db)` as the last two parameters, in that order. Never construct a session or resolve auth manually inside a handler.

**Return values:** route handlers return typed Pydantic response models (`response_model=SenderResponse` on the decorator + a matching return type), not raw dicts, except for a handful of legacy/simple endpoints (`spambot-check` returns a plain dict). Prefer a typed schema for anything new.

## Module Design

**Workspace scoping is load-bearing, not optional.** Every new query/table touching tenant data must filter by `workspace_id` (usually `ctx.workspace_id` from `AuthCtx`). Grep for `# TODO(v2-rls): replaced by RLS policy` comments — this marks every spot the codebase acknowledges app-level tenant isolation as a temporary stand-in for Postgres RLS; don't remove the app-level filter to "simplify" until RLS actually lands.

**Config:** all settings live in `app/config.py::Settings(BaseSettings)` (pydantic-settings, reads `.env`). New env-driven knobs are added as a new `Field(default=..., validation_alias="ENV_VAR_NAME", description="...")` with an explanatory `description` — every existing field has one, follow suit; the description is effectively the only place these knobs are documented.

**Barrel files:** `app/models/__init__.py` and `app/schemas/__init__.py` act as barrel modules — `from app.models import Sender, Workspace, ...` and `from app.schemas import SenderCreate, ...` are the standard import shape; do not import from a private submodule path.

## Migration Conventions (raw SQL, not ORM-managed)

- File name: `NNN_short_name.sql`, sequential 3-digit, referenced by exact filename in `tests/conftest.py::_build_outreach_schema` (recent migrations 038+ use an `if _mig_0NN.exists(): ...` exists-guard so conftest changes land ahead of the actual migration file and stay green).
- **Idempotency is mandatory**: `CREATE TABLE IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`, `DO $$ ... EXCEPTION WHEN duplicate_object THEN NULL; END $$;` for constraints, `ON CONFLICT DO NOTHING` for seed rows. The auto-applier (`app/database.py::_apply_migrations`) reruns any migration not yet recorded in `schema_migrations`, so a non-idempotent file breaks fresh-DB bootstrap.
- A migration that only DROPs a column/constraint needs **no corresponding conftest block** — `Base.metadata.create_all` simply mirrors whatever the ORM currently declares (see migration `059` comment in conftest.py:290-296 as the canonical example of this reasoning).
- Never use Alembic (explicitly forbidden by root CLAUDE.md).

---

*Convention analysis: 2026-07-09*
