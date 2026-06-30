"""
Database engine + session factory + startup initialization for outreach-platform.

`init_db()` is called from FastAPI lifespan (app/main.py). It does TWO things:
  1. `Base.metadata.create_all` — creates ORM-declared tables (no-op if exist).
  2. `_apply_migrations` — runs every `migrations/*.sql` not yet recorded in
     `schema_migrations`, in lexical filename order, idempotently.

The second step exists because the original telegram-api convention was "raw-SQL
migrations applied by hand". 2026-05-26 incident proved that's too fragile —
after a DROP SCHEMA, `create_all` rebuilt ORM columns but raw-SQL additions
(e.g. `senders.telegram_id` from 006) were silently lost. Auto-applier closes
that gap.

Convention for new migrations:
  - File name `NNN_short_name.sql` (3-digit prefix for lexical sort).
  - Idempotent: use `IF NOT EXISTS` for tables/indexes; `DO $$ EXCEPTION
    duplicate_object $$` for constraints; `ON CONFLICT DO NOTHING` for seeds.
  - The file may include `BEGIN; ... COMMIT;` for atomic groups — asyncpg's
    simple-query path respects them. Without explicit BEGIN/COMMIT, each
    statement auto-commits.
  - A failing migration is NOT recorded in `schema_migrations`, so the api
    will retry it on next start — but it will keep failing until fixed.
    Half-applied state on idempotent migrations converges on retry; on
    non-idempotent ones the human must intervene.
  - Bootstrap migration is `_schema_migrations.sql` (underscore prefix sorts
    first); it's never recorded in the tracking table.
"""
import hashlib
import logging
import pathlib
from typing import List, Tuple

import asyncpg
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from app.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

# Convert postgresql:// to postgresql+asyncpg://
database_url = settings.database_url.replace(
    "postgresql://", "postgresql+asyncpg://"
)

# Raw asyncpg DSN (strip the +asyncpg suffix) for the applier — it talks to
# postgres directly so multi-statement migration files (with their own
# BEGIN/COMMIT) don't fight SQLAlchemy's transaction context.
_asyncpg_dsn = settings.database_url.replace(
    "postgresql+asyncpg://", "postgresql://", 1
)

engine = create_async_engine(
    database_url,
    echo=settings.log_level == "DEBUG",
    pool_size=5,
    max_overflow=10,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

Base = declarative_base()


# --- Applier ------------------------------------------------------------------

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"
# Static id used by pg_advisory_lock — any signed 64-bit int. Distinct from
# other apps to avoid collision if multi-tenant pg ever happens.
APPLIER_LOCK_KEY = 7261_8417_2026_0526  # "RAW_BIRTHDATE" — arbitrary but stable


def _list_migration_files() -> List[pathlib.Path]:
    """Return sorted migration files. `_schema_migrations.sql` first (underscore
    sorts before digits in lexical order)."""
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


async def _apply_migrations() -> None:
    """Run every migration not yet in `schema_migrations`, idempotently, behind
    a session-level advisory lock so multiple api instances starting in parallel
    don't race.

    Failure semantics:
      - Bootstrap (`_schema_migrations.sql`) failure → raise; api start fails.
      - Per-migration failure → record nothing, raise; api start fails.
        The migration will be retried on next start. Half-applied state on
        idempotent migrations converges; on non-idempotent ones, fix the file
        and re-deploy.
    """
    files = _list_migration_files()
    if not files:
        logger.info("[migrate] no migration files found in %s", MIGRATIONS_DIR)
        return

    conn = await asyncpg.connect(dsn=_asyncpg_dsn)
    try:
        await conn.execute(f"SELECT pg_advisory_lock({APPLIER_LOCK_KEY})")
        try:
            # 1. Bootstrap (always runs first; never recorded in tracking table).
            bootstrap = MIGRATIONS_DIR / "_schema_migrations.sql"
            if bootstrap.exists():
                logger.info("[migrate] bootstrap _schema_migrations.sql")
                await conn.execute(bootstrap.read_text())
            else:
                logger.warning(
                    "[migrate] _schema_migrations.sql missing — applier cannot track state"
                )
                return

            # 2. Load applied versions.
            rows = await conn.fetch("SELECT version FROM schema_migrations")
            applied = {r["version"] for r in rows}

            # 3. Run each pending migration in order.
            pending: List[Tuple[str, pathlib.Path, str]] = []
            for f in files:
                if f.name.startswith("_"):
                    continue  # bootstrap, already handled
                version = f.stem  # filename without .sql
                if version in applied:
                    continue
                content = f.read_text()
                sha = hashlib.sha256(content.encode()).hexdigest()
                pending.append((version, f, sha))

            if not pending:
                logger.info("[migrate] schema is up to date (%d applied)", len(applied))
                return

            logger.info("[migrate] applying %d pending migration(s)", len(pending))
            for version, path, sha in pending:
                logger.info("[migrate] -> %s", version)
                content = path.read_text()
                try:
                    await conn.execute(content)
                except Exception as exc:
                    logger.error("[migrate] FAIL %s: %s", version, exc)
                    raise
                await conn.execute(
                    "INSERT INTO schema_migrations(version, sha256) VALUES ($1, $2)",
                    version,
                    sha,
                )
                logger.info("[migrate] OK   %s", version)
        finally:
            await conn.execute(f"SELECT pg_advisory_unlock({APPLIER_LOCK_KEY})")
    finally:
        await conn.close()


# --- Session factory ----------------------------------------------------------


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    async with engine.begin() as conn:
        # pgvector: the `vector` type must exist BEFORE create_all, because
        # KbChunk.embedding is declared Vector(1536). Migration 041 also creates
        # the extension (idempotently), but migrations run AFTER create_all — so
        # on a fresh DB / post-DROP-SCHEMA recovery (`docker compose up -d --build api`)
        # create_all would fail with `type "vector" does not exist` before any
        # migration could run. Creating it up-front here keeps the documented
        # recovery path working. IF NOT EXISTS makes it idempotent.
        await conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
        await conn.run_sync(Base.metadata.create_all)
    # Auto-apply raw-SQL migrations after the ORM baseline is in place.
    # See module docstring for rationale.
    await _apply_migrations()
