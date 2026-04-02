"""
Proxy pool management for Decodo ISP static residential proxies.

POST /api/v1/proxy-pool/init  — populate pool from DECODO_* env vars (idempotent)
GET  /api/v1/proxy-pool       — view pool status (free / assigned)
POST /api/v1/proxy-pool/add   — add a single port to the pool (no .env edit required)
"""
import logging
import time
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database import get_db
from app.models import ProxyPool, Sender
from app.routers.auth import verify_api_key
from app.services.telegram import telegram_service, build_proxy_tuple

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/api/v1/proxy-pool", tags=["proxy-pool"])


# === Schemas ===

class ProxyPoolInitResponse(BaseModel):
    added: int
    total: int


class ProxyPoolAddRequest(BaseModel):
    port: int


class ProxyPoolAddResponse(BaseModel):
    port: int
    added: bool  # False if already existed


class ProxyTestResponse(BaseModel):
    ok: bool
    sender_slug: str
    proxy_host: Optional[str] = None
    proxy_port: Optional[int] = None
    latency_ms: Optional[int] = None
    telegram_id: Optional[int] = None
    username: Optional[str] = None
    error: Optional[str] = None


class ProxyPoolEntryResponse(BaseModel):
    id: UUID
    host: str
    port: int
    is_free: bool
    assigned_to_sender_id: Optional[UUID] = None
    assigned_to_sender_slug: Optional[str] = None


class ProxyPoolListResponse(BaseModel):
    total: int
    free: int
    assigned: int
    proxies: List[ProxyPoolEntryResponse]


# === Endpoints ===

@router.post("/init", response_model=ProxyPoolInitResponse)
async def init_proxy_pool(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    """
    Populate the proxy pool from DECODO_* environment variables.

    Reads DECODO_HOST, DECODO_USERNAME, DECODO_PASSWORD, DECODO_PORTS from .env.
    Idempotent — already existing (host, port) entries are skipped (ON CONFLICT DO NOTHING).
    """
    if not all([settings.decodo_host, settings.decodo_username, settings.decodo_ports]):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Decodo credentials not configured. Set DECODO_HOST, DECODO_USERNAME, DECODO_PASSWORD, DECODO_PORTS in .env",
                "code": "DECODO_NOT_CONFIGURED",
            },
        )

    ports = [
        int(p.strip())
        for p in settings.decodo_ports.split(",")
        if p.strip()
    ]

    if not ports:
        raise HTTPException(
            status_code=400,
            detail={"error": "DECODO_PORTS is empty or invalid", "code": "DECODO_PORTS_INVALID"},
        )

    added = 0
    for port in ports:
        result = await db.execute(
            text(
                "INSERT INTO proxy_pool (host, port, username, password) "
                "VALUES (:host, :port, :username, :password) "
                "ON CONFLICT (host, port) DO UPDATE "
                "SET username = EXCLUDED.username, password = EXCLUDED.password"
            ),
            {
                "host": settings.decodo_host,
                "port": port,
                "username": settings.decodo_username,
                "password": settings.decodo_password,
            },
        )
        added += result.rowcount

    await db.commit()

    total_result = await db.execute(select(func.count()).select_from(ProxyPool))
    total = total_result.scalar_one()

    logger.info(f"[proxy-pool] init: added={added}, total={total}")
    return ProxyPoolInitResponse(added=added, total=total)


@router.get("", response_model=ProxyPoolListResponse)
async def list_proxy_pool(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    """
    View current proxy pool status.

    Shows all proxies with their assignment status.
    is_free=true means the proxy is available for a new account.
    """
    result = await db.execute(
        select(ProxyPool)
        .options(selectinload(ProxyPool.sender))
        .order_by(ProxyPool.port)
    )
    entries = result.scalars().all()

    proxies = [
        ProxyPoolEntryResponse(
            id=entry.id,
            host=entry.host,
            port=entry.port,
            is_free=entry.assigned_to_sender_id is None,
            assigned_to_sender_id=entry.assigned_to_sender_id,
            assigned_to_sender_slug=entry.sender.slug if entry.sender else None,
        )
        for entry in entries
    ]

    free_count = sum(1 for p in proxies if p.is_free)

    return ProxyPoolListResponse(
        total=len(proxies),
        free=free_count,
        assigned=len(proxies) - free_count,
        proxies=proxies,
    )


@router.post("/add", response_model=ProxyPoolAddResponse)
async def add_proxy_to_pool(
    body: ProxyPoolAddRequest,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    """
    Add a single port to the proxy pool using existing DECODO_* credentials.

    Useful for adding new ports without editing .env or restarting the container.
    Idempotent — if the (host, port) already exists, returns added=false.
    """
    if not all([settings.decodo_host, settings.decodo_username]):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Decodo credentials not configured. Set DECODO_HOST, DECODO_USERNAME, DECODO_PASSWORD in .env",
                "code": "DECODO_NOT_CONFIGURED",
            },
        )

    result = await db.execute(
        text(
            "INSERT INTO proxy_pool (host, port, username, password) "
            "VALUES (:host, :port, :username, :password) "
            "ON CONFLICT (host, port) DO UPDATE "
            "SET username = EXCLUDED.username, password = EXCLUDED.password"
        ),
        {
            "host": settings.decodo_host,
            "port": body.port,
            "username": settings.decodo_username,
            "password": settings.decodo_password,
        },
    )
    added = result.rowcount > 0
    await db.commit()

    logger.info(f"[proxy-pool] add port={body.port}: added={added}")
    return ProxyPoolAddResponse(port=body.port, added=added)


@router.get("/test/{sender_slug}", response_model=ProxyTestResponse)
async def test_sender_proxy(
    sender_slug: str,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    """
    Test that a sender's proxy and Telegram session are working.

    Connects to Telegram through the sender's assigned proxy and calls get_me().
    Returns latency, Telegram ID, and username on success, or an error message on failure.
    """
    result = await db.execute(select(Sender).where(Sender.slug == sender_slug))
    sender = result.scalar_one_or_none()

    if sender is None:
        raise HTTPException(status_code=404, detail={"error": f"Sender '{sender_slug}' not found"})

    proxy_info = sender.proxy  # may be None

    client = None
    t0 = time.monotonic()
    try:
        client = await telegram_service.get_client(
            sender.slug,
            sender.session_string,
            proxy=proxy_info,
        )
        me = await client.get_me()
        latency_ms = int((time.monotonic() - t0) * 1000)

        logger.info(f"[proxy-pool] test {sender_slug}: ok, latency={latency_ms}ms, user=@{me.username}")
        return ProxyTestResponse(
            ok=True,
            sender_slug=sender_slug,
            proxy_host=proxy_info["host"] if proxy_info else None,
            proxy_port=proxy_info["port"] if proxy_info else None,
            latency_ms=latency_ms,
            telegram_id=me.id,
            username=me.username,
        )
    except Exception as e:
        latency_ms = int((time.monotonic() - t0) * 1000)
        error_msg = str(e)
        logger.warning(f"[proxy-pool] test {sender_slug}: FAILED ({error_msg})")
        return ProxyTestResponse(
            ok=False,
            sender_slug=sender_slug,
            proxy_host=proxy_info["host"] if proxy_info else None,
            proxy_port=proxy_info["port"] if proxy_info else None,
            latency_ms=latency_ms,
            error=error_msg,
        )
    finally:
        if client is not None:
            await telegram_service.disconnect_client(client)
