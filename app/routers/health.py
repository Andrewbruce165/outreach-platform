from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from datetime import datetime, timezone
from app.database import get_db
from app.models import Sender
from app.schemas import HealthResponse, SendersHealth
from app.routers.auth import verify_api_key
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
    
    # Get senders stats
    total = 0
    active = 0
    try:
        result = await db.execute(select(Sender))
        senders = result.scalars().all()
        total = len(senders)
        active = sum(1 for s in senders if s.is_active)
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


@router.get("/health/detailed")
async def detailed_health(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    """Per-sender queue statistics and account state."""
    now = datetime.now(timezone.utc)
    rows = await db.execute(text("""
        SELECT s.slug, s.is_active, s.role,
               COUNT(q.id) FILTER (WHERE q.status = 'pending')                              AS pending,
               COUNT(q.id) FILTER (WHERE q.status = 'processing')                           AS processing,
               COUNT(q.id) FILTER (WHERE q.status = 'sent'
                   AND q.finished_at >= NOW() - INTERVAL '1 hour')                          AS sent_last_hour,
               COUNT(q.id) FILTER (WHERE q.status = 'sent'
                   AND q.finished_at >= NOW() - INTERVAL '24 hours')                        AS sent_last_day,
               MAX(q.finished_at) FILTER (WHERE q.status = 'sent')                          AS last_sent_at,
               COUNT(q.id) FILTER (WHERE q.error_message LIKE 'FloodWait%'
                   AND q.finished_at >= NOW() - INTERVAL '1 hour')                          AS flood_waits_last_hour
        FROM senders s
        LEFT JOIN message_queue q ON q.sender_id = s.id
        WHERE s.is_active = true
        GROUP BY s.id, s.slug, s.is_active, s.role
        ORDER BY s.slug
    """))
    senders = [dict(r._mapping) for r in rows.fetchall()]
    return {"senders": senders, "timestamp": now.isoformat()}
