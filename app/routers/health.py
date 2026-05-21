"""Public /api/v1/health endpoint.

Phase 02.1 (CR-07): public response отдаёт только технический статус
({status, database, version, uptime_seconds}) — без sender-counts из всех
workspaces. Раньше anonymous GET раскрывал total/active sender'ов по
системе, что для multi-tenant SaaS — information disclosure (business
intelligence о размере платформы).

Workspace-scoped detailed health (per-tenant sender stats, queue depth и т.п.)
будет добавлен отдельным endpoint'ом /health/detailed под Depends(auth_dep)
в более поздней фазе — он тогда вернёт счётчики только для ctx.workspace_id.
"""

import time

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db

router = APIRouter(prefix="/api/v1", tags=["health"])

# Track startup time
START_TIME = time.time()
VERSION = "1.0.0"


@router.get("/health")
async def health_check(db: AsyncSession = Depends(get_db)):
    """Public health check (CR-07): только тех-статус, никаких per-tenant aggregates."""
    db_status = "connected"
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        db_status = "disconnected"

    uptime = int(time.time() - START_TIME)
    return {
        "status": "healthy" if db_status == "connected" else "unhealthy",
        "database": db_status,
        "version": VERSION,
        "uptime_seconds": uptime,
    }


# NOTE(phase-02.1, CR-07): legacy /health/detailed remains intentionally
# disabled. Re-introduce later under Depends(auth_dep) with workspace_id
# scoping — returning only ctx.workspace_id's senders/queue stats.
