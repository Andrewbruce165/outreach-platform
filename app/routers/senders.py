import logging
import subprocess
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models import Sender, AIContext
from app.schemas import SenderCreate, SenderUpdate, SenderResponse, SenderListResponse
from app.services.encryption import encrypt_session
from app.routers.auth import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/senders", tags=["senders"])


def sender_to_response(sender: Sender) -> dict:
    """Convert Sender model to response dict with ai_context_name."""
    data = {
        "id": sender.id,
        "slug": sender.slug,
        "name": sender.name,
        "phone": sender.phone,
        "is_active": sender.is_active,
        "role": sender.role,
        "auth_status": sender.auth_status,
        "proxy": sender.proxy,
        "ai_context_id": sender.ai_context_id,
        "ai_context_name": sender.ai_context.name if sender.ai_context else None,
        "last_used_at": sender.last_used_at,
        "created_at": sender.created_at
    }
    return data


def _restart_listener(reason: str):
    """Restart the telegram-listener container.

    Only called when a change affects which accounts the listener should track
    (i.e. a role='sender' account was added/updated, or a role changed).
    """
    try:
        subprocess.run(
            ["docker", "restart", "telegram-listener"],
            capture_output=True,
            timeout=10,
        )
        logger.info(f"✅ Listener перезапущен: {reason}")
    except Exception as e:
        logger.warning(f"⚠️ Не удалось перезапустить Listener: {e}")


@router.get("", response_model=SenderListResponse)
async def list_senders(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key)
):
    """List all senders."""
    from sqlalchemy.orm import selectinload
    result = await db.execute(
        select(Sender).options(selectinload(Sender.ai_context)).order_by(Sender.name)
    )
    senders = result.scalars().all()
    return SenderListResponse(senders=[SenderResponse(**sender_to_response(s)) for s in senders])


@router.post("", response_model=SenderResponse, status_code=201)
async def create_sender(
    request: SenderCreate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key)
):
    """Add new sender."""
    # Check if slug exists
    existing = await db.execute(
        select(Sender).where(Sender.slug == request.slug)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"Sender with slug '{request.slug}' already exists")

    # Encrypt session string
    encrypted_session = encrypt_session(request.session_string)

    role = request.role or "sender"

    sender = Sender(
        slug=request.slug,
        name=request.name,
        phone=request.phone,
        session_string=encrypted_session,
        is_active=True,
        role=role,
        proxy=request.proxy.model_dump() if request.proxy else None,
        ai_context_id=request.ai_context_id
    )
    db.add(sender)
    await db.commit()
    await db.refresh(sender)

    # Reload with relationship
    from sqlalchemy.orm import selectinload
    result = await db.execute(
        select(Sender).options(selectinload(Sender.ai_context)).where(Sender.id == sender.id)
    )
    sender = result.scalar_one()

    # Proxy pool linking
    from app.models import ProxyPool

    if not sender.proxy:
        # No proxy passed — auto-assign first free proxy from pool
        pool_result = await db.execute(
            select(ProxyPool)
            .where(ProxyPool.assigned_to_sender_id.is_(None))
            .limit(1)
        )
        pool_entry = pool_result.scalar_one_or_none()
        if pool_entry is not None:
            sender.proxy = {
                "type": "socks5",
                "host": pool_entry.host,
                "port": pool_entry.port,
                "username": pool_entry.username,
                "password": pool_entry.password,
            }
            pool_entry.assigned_to_sender_id = sender.id
            await db.commit()
            await db.refresh(sender)
            logger.info(f"[proxy-pool] auto-assigned port {pool_entry.port} → sender {sender.slug}")
        else:
            logger.warning(f"[proxy-pool] no free proxy available for sender {sender.slug}, proceeding without proxy")
    else:
        # Proxy passed explicitly — link to pool entry if it exists
        pool_result = await db.execute(
            select(ProxyPool).where(
                ProxyPool.host == sender.proxy["host"],
                ProxyPool.port == sender.proxy["port"],
            )
        )
        pool_entry = pool_result.scalar_one_or_none()
        if pool_entry is not None:
            pool_entry.assigned_to_sender_id = sender.id
            await db.commit()
            logger.info(f"[proxy-pool] assigned port {sender.proxy['port']} → sender {sender.slug}")

    # Restart listener only for sender-role accounts (checkers don't need listening)
    if sender.role == "sender":
        _restart_listener(f"new sender created: {sender.slug}")

    return SenderResponse(**sender_to_response(sender))


@router.get("/{slug}", response_model=SenderResponse)
async def get_sender(
    slug: str,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key)
):
    """Get sender by slug."""
    from sqlalchemy.orm import selectinload
    result = await db.execute(
        select(Sender).options(selectinload(Sender.ai_context)).where(Sender.slug == slug)
    )
    sender = result.scalar_one_or_none()

    if not sender:
        raise HTTPException(status_code=404, detail=f"Sender '{slug}' not found")

    return SenderResponse(**sender_to_response(sender))


@router.patch("/{slug}", response_model=SenderResponse)
async def update_sender(
    slug: str,
    request: SenderUpdate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key)
):
    """Update sender."""
    result = await db.execute(select(Sender).where(Sender.slug == slug))
    sender = result.scalar_one_or_none()

    if not sender:
        raise HTTPException(status_code=404, detail=f"Sender '{slug}' not found")

    # Remember old role before applying updates — needed to detect role changes
    old_role = sender.role

    if request.name is not None:
        sender.name = request.name
    if request.phone is not None:
        sender.phone = request.phone
    if request.session_string is not None:
        sender.session_string = encrypt_session(request.session_string)
    if request.is_active is not None:
        sender.is_active = request.is_active
    if request.ai_context_id is not None:
        sender.ai_context_id = request.ai_context_id
    if request.role is not None:
        sender.role = request.role
    if request.proxy is not None:
        sender.proxy = request.proxy.model_dump()

    await db.commit()
    await db.refresh(sender)

    # Reload with relationship
    from sqlalchemy.orm import selectinload
    result = await db.execute(
        select(Sender).options(selectinload(Sender.ai_context)).where(Sender.id == sender.id)
    )
    sender = result.scalar_one()

    new_role = sender.role

    # Restart listener when:
    # - Account is (or was) a 'sender' — listener tracks sender-role accounts only.
    # - Role changed in either direction (sender→checker removes it from listener,
    #   checker→sender adds it to listener).
    should_restart = (new_role == "sender") or (old_role == "sender" and old_role != new_role)
    if should_restart:
        _restart_listener(f"sender updated: {sender.slug} (role: {old_role} → {new_role})")

    return SenderResponse(**sender_to_response(sender))


@router.delete("/{slug}", status_code=204)
async def delete_sender(
    slug: str,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key)
):
    """
    Полностью удалить агента (hard delete).
    ВНИМАНИЕ: Удаляются также все диалоги и сообщения этого агента!
    """
    from sqlalchemy import text

    result = await db.execute(select(Sender).where(Sender.slug == slug))
    sender = result.scalar_one_or_none()

    if not sender:
        raise HTTPException(status_code=404, detail=f"Sender '{slug}' not found")

    sender_id = str(sender.id)

    # Удаляем сообщения всех диалогов этого sender
    await db.execute(
        text("DELETE FROM messages WHERE conversation_id IN (SELECT id FROM conversations WHERE sender_id = :sid)"),
        {"sid": sender_id}
    )

    # Удаляем диалоги sender (CASCADE должен сработать, но на всякий случай)
    await db.execute(
        text("DELETE FROM conversations WHERE sender_id = :sid"),
        {"sid": sender_id}
    )

    # Удаляем кэш контактов
    await db.execute(
        text("DELETE FROM contacts_cache WHERE sender_id = :sid"),
        {"sid": sender_id}
    )

    # Удаляем логи сообщений
    await db.execute(
        text("DELETE FROM messages_log WHERE sender_id = :sid"),
        {"sid": sender_id}
    )

    # Удаляем самого sender
    await db.delete(sender)
    await db.commit()


@router.get("/{slug}/spambot-check")
async def check_spambot(
    slug: str,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key)
):
    """Check sender's account status via @SpamBot.

    Returns:
        status: 'free' | 'limited' | 'suspended' | 'unknown'
        raw_text: full SpamBot response
    """
    from app.services.telegram import telegram_service, SessionAuthError

    result = await db.execute(select(Sender).where(Sender.slug == slug))
    sender = result.scalar_one_or_none()
    if not sender:
        raise HTTPException(status_code=404, detail="Sender not found")
    if not sender.session_string:
        raise HTTPException(status_code=400, detail="Sender has no session")

    client = None
    try:
        client = await telegram_service.get_client(sender.slug, sender.session_string, proxy=sender.proxy)
        spambot_result = await telegram_service.check_spambot(client)

        # Update auth_status based on SpamBot response
        status_map = {"limited": "limited", "suspended": "banned", "free": "ok"}
        new_auth_status = status_map.get(spambot_result["status"])
        if new_auth_status and sender.auth_status != new_auth_status:
            sender.auth_status = new_auth_status
            if new_auth_status in ("limited", "banned"):
                sender.is_active = False
            await db.commit()
            spambot_result["auth_status_updated"] = new_auth_status

        return spambot_result
    except SessionAuthError as e:
        raise HTTPException(status_code=403, detail={
            "error": f"Session auth failed: {e.auth_status}",
            "code": "AUTH_ERROR",
            "auth_status": e.auth_status
        })
    except Exception as e:
        logger.error(f"SpamBot check failed for {slug}: {e}")
        raise HTTPException(status_code=500, detail=f"Check failed: {str(e)}")
    finally:
        if client:
            await telegram_service.disconnect_client(client)
