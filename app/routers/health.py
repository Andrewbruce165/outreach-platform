from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from app.database import get_db
from app.models import Sender
from app.schemas import HealthResponse, SendersHealth
# NOTE(phase-1, D-14): legacy verify_api_key removed. /health/detailed is
# disabled in Phase 1 — it will be re-introduced behind auth_dep in Phase 2-4
# alongside the rewrite of business routers.
import time

router = APIRouter(prefix="/api/v1", tags=["health"])

# Track startup time
START_TIME = time.time()
VERSION = "1.0.0"


@router.get("/health", response_model=HealthResponse)
async def health_check(db: AsyncSession = Depends(get_db)):
    """Health check endpoint."""
    
    # Check database
    db_status = "connected"
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        db_status = "disconnected"
    
    # Get senders stats.
    # Phase 2 (D-11/D-12): senders.is_active dropped — derived "active" =
    # lifecycle_status == 'active' AND auth_status == 'ok'.
    total = 0
    active = 0
    try:
        result = await db.execute(select(Sender))
        senders = result.scalars().all()
        total = len(senders)
        active = sum(
            1 for s in senders
            if s.lifecycle_status == "active" and s.auth_status == "ok"
        )
    except Exception:
        pass
    
    uptime = int(time.time() - START_TIME)
    
    return HealthResponse(
        status="healthy" if db_status == "connected" else "unhealthy",
        database=db_status,
        senders=SendersHealth(
            total=total,
            active=active,
            sessions_valid=active  # Simplified for now
        ),
        version=VERSION,
        uptime_seconds=uptime
    )


# NOTE(phase-1, D-14): /health/detailed temporarily removed — it depended on
# legacy verify_api_key (now deleted). To be re-added behind auth_dep with
# workspace_id scoping when senders router is rewritten in Phase 2.
