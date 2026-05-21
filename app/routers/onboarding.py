"""Onboarding router (Phase 2 — ONBD-01..05).

All endpoints are workspace-scoped via ``Depends(auth_dep)`` (Phase 1 D-12).
Onboarding state lives in the ``onboarding_sessions`` table (D-16) — the
in-memory ``_onboarding_sessions: dict`` of the legacy router is gone.

Endpoints (12):
    POST   /api/v1/onboarding/start                   — phone + SMS-code flow start
    POST   /api/v1/onboarding/verify-code             — code → sender create OR 2fa_required
    POST   /api/v1/onboarding/verify-2fa              — 2FA password → sender create
    POST   /api/v1/onboarding/qr-start                — QR login flow start (creates sender on scan)
    GET    /api/v1/onboarding/qr-status/{session_id}  — poll QR flow status
    DELETE /api/v1/onboarding/cancel/{session_id}     — abort flow + remove row + disconnect
    POST   /api/v1/onboarding/reauth/{sender_slug}    — phone-code reauth for existing sender
    POST   /api/v1/onboarding/reauth/qr/{sender_slug} — QR reauth for existing sender

Persistence model (D-17):
    * onboarding_sessions row    — phone_code_hash + encrypted session_string + status,
                                   workspace-isolated, TTL 10 min, cleaned by
                                   OnboardingCleanupWorker.
    * _in_process_clients dict   — TelegramClient objects keyed by session_id
                                   (Telethon clients are NOT serialisable).
    * On cache-miss (e.g. api-container restart between /start and /verify-code),
      _get_or_recover_client decrypts session_string and reconnects Telethon —
      the saved string already carries DC routing from send_code_request, so
      the user does not have to retype the phone.

subprocess.run('docker restart telegram-listener') is GONE (D-18) — the
listener picks up new senders on its periodic reconcile loop (plan 02-01 task 3).
"""

import asyncio
import base64
import io
import logging
from io import BytesIO
from typing import Optional
from uuid import UUID

import qrcode
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberBannedError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
)
from telethon.sessions import StringSession

from app.database import AsyncSessionLocal, get_db
from app.models import OnboardingSession, ProxyPool, Sender
from app.schemas import ProxyConfig
from app.services.encryption import decrypt_session, encrypt_session
from app.services.onboarding_state import (
    delete_session,
    load_state,
    save_state,
    update_status,
)
from app.services.telegram import make_telegram_client
from app.utils.auth import AuthCtx, auth_dep

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/onboarding", tags=["onboarding"])

# D-17: Telethon client objects (not serialisable) live in-process, keyed by
# onboarding session_id. Recovery from DB is done via _get_or_recover_client.
_in_process_clients: dict[str, TelegramClient] = {}


# ─── Schemas (inline; not promoted to app/schemas because onboarding-only) ───


class StartRequest(BaseModel):
    phone: str = Field(..., description="Phone with country code, e.g. +79001234567")
    role: Optional[str] = Field("sender", description="'sender' or 'checker'")
    proxy_id: Optional[UUID] = Field(
        None, description="If provided, pick this proxy from the workspace pool."
    )


class StartResponse(BaseModel):
    session_id: UUID
    status: str
    phone: str


class VerifyCodeRequest(BaseModel):
    session_id: UUID
    code: str
    role: Optional[str] = Field(None, description="Optional override at verify time.")
    name: Optional[str] = None


class Verify2FARequest(BaseModel):
    session_id: UUID
    password: str
    name: Optional[str] = None


class QrStartRequest(BaseModel):
    role: Optional[str] = Field("sender")
    proxy_id: Optional[UUID] = None


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _normalize_phone(raw: str) -> Optional[str]:
    """Light E.164 normalisation for the onboarding path.

    Full normaliser will live in ``app/utils/phone.py`` (plan 02-04).
    Here we only strip whitespace/hyphens and prepend ``+`` if absent — the
    Telethon ``send_code_request`` itself validates the rest and bubbles up
    PhoneNumberInvalidError otherwise.
    """
    if not raw:
        return None
    phone = raw.strip().replace(" ", "").replace("-", "")
    if not phone:
        return None
    if not phone.startswith("+"):
        phone = "+" + phone
    return phone


async def _resolve_proxy(
    db: AsyncSession,
    workspace_id: UUID,
    proxy_id: Optional[UUID],
) -> Optional[dict]:
    """Resolve a proxy from the workspace pool. ``None`` if proxy_id is None."""
    if proxy_id is None:
        return None
    result = await db.execute(
        select(ProxyPool).where(
            ProxyPool.id == proxy_id,
            ProxyPool.workspace_id == workspace_id,
            # TODO(v2-rls): replaced by RLS policy app.workspace_id
        )
    )
    proxy_row = result.scalars().first()
    if proxy_row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "PROXY_NOT_FOUND",
                "message": "Proxy not found in workspace pool",
            },
        )
    return {
        "type": "socks5",
        "host": proxy_row.host,
        "port": proxy_row.port,
        "username": proxy_row.username,
        "password": proxy_row.password,
    }


async def _get_or_recover_client(session_row: OnboardingSession) -> TelegramClient:
    """Return the hot in-process client OR rebuild it from DB state (D-17).

    Because ``session_string`` is captured AFTER ``send_code_request`` it
    already includes DC routing — Telethon can reconnect and the user does
    not need to retype the phone after an api-container restart.
    """
    sid_str = str(session_row.id)
    client = _in_process_clients.get(sid_str)
    if client is not None:
        return client
    logger.info(
        f"[onboarding] recovering Telethon client from DB: session={sid_str[:8]}"
    )
    session_string = decrypt_session(session_row.encrypted_session_string)
    client = make_telegram_client(StringSession(session_string), proxy=session_row.proxy)
    await client.connect()
    _in_process_clients[sid_str] = client
    return client


def _drop_in_process(session_id: UUID) -> None:
    _in_process_clients.pop(str(session_id), None)


async def _safe_disconnect(client: Optional[TelegramClient]) -> None:
    if client is None:
        return
    try:
        if client.is_connected():
            await client.disconnect()
    except Exception as e:  # noqa: BLE001 — best-effort cleanup
        logger.warning(f"[onboarding] disconnect error: {e}")


async def _create_sender_from_session(
    db: AsyncSession,
    ctx: AuthCtx,
    session_row: OnboardingSession,
    client: TelegramClient,
    name: Optional[str] = None,
) -> Sender:
    """After successful sign_in / qr scan — create a Sender in the workspace.

    Default ``lifecycle_status='active'`` (D-12) and ``auth_status='ok'``;
    rate_per_* default to the migration server_defaults (4/20/150).
    """
    session_string = client.session.save()
    try:
        me = await client.get_me()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[onboarding] get_me failed: {e}")
        me = None

    tg_id = getattr(me, "id", None)
    first_name = getattr(me, "first_name", None) or ""
    suffix = str(tg_id) if tg_id is not None else str(session_row.id)[:8]
    slug = f"sender-{suffix}"

    sender = Sender(
        workspace_id=ctx.workspace_id,
        slug=slug,
        name=name or first_name or slug,
        phone=session_row.phone or "",
        session_string=encrypt_session(session_string),
        role=session_row.role,
        proxy=session_row.proxy,
        auth_status="ok",
        lifecycle_status="active",
        # rate_per_* server_default = 4 / 20 / 150 (migration 013)
    )
    db.add(sender)
    await db.commit()
    await db.refresh(sender)
    logger.info(
        f"[onboarding] sender created slug={slug} role={session_row.role} "
        f"workspace={str(ctx.workspace_id)[:8]} "
        f"phone={(session_row.phone or '')[:6]}***"
    )
    return sender


def _map_telethon_error(e: Exception) -> HTTPException:
    """Map common Telethon errors to structured HTTPException."""
    if isinstance(e, PhoneCodeInvalidError):
        return HTTPException(
            status_code=400,
            detail={"code": "PHONE_CODE_INVALID", "message": "Invalid SMS code"},
        )
    if isinstance(e, PhoneCodeExpiredError):
        return HTTPException(
            status_code=400,
            detail={"code": "PHONE_CODE_EXPIRED", "message": "SMS code expired"},
        )
    if isinstance(e, PasswordHashInvalidError):
        return HTTPException(
            status_code=400,
            detail={"code": "PASSWORD_INVALID", "message": "Invalid 2FA password"},
        )
    if isinstance(e, PhoneNumberBannedError):
        return HTTPException(
            status_code=400,
            detail={"code": "PHONE_BANNED", "message": "Phone banned by Telegram"},
        )
    if isinstance(e, PhoneNumberInvalidError):
        return HTTPException(
            status_code=400,
            detail={"code": "PHONE_INVALID", "message": "Invalid phone number"},
        )
    if isinstance(e, FloodWaitError):
        return HTTPException(
            status_code=429,
            detail={
                "code": "FLOOD_WAIT",
                "message": f"Telegram rate limit: retry after {e.seconds}s",
                "retry_after": e.seconds,
            },
        )
    return HTTPException(
        status_code=500,
        detail={"code": "TELETHON_ERROR", "message": str(e)},
    )


def _make_qr_image(url: str) -> str:
    img = qrcode.make(url)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"


# ─── POST /onboarding/start ──────────────────────────────────────────────────


@router.post("/start", response_model=StartResponse)
async def start_onboarding(
    request: StartRequest,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Send code to phone; persist state; return ``session_id``."""
    phone = _normalize_phone(request.phone)
    if phone is None:
        raise HTTPException(
            status_code=400,
            detail={"code": "PHONE_INVALID", "message": "Invalid phone format"},
        )
    role = request.role or "sender"
    if role not in ("sender", "checker"):
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_ROLE", "message": f"Invalid role: {role}"},
        )

    proxy = await _resolve_proxy(db, ctx.workspace_id, request.proxy_id)

    client = make_telegram_client(StringSession(), proxy=proxy)
    sent_code = None
    try:
        await client.connect()
        sent_code = await client.send_code_request(phone)
    except (
        PhoneNumberInvalidError,
        PhoneNumberBannedError,
        FloodWaitError,
    ) as e:
        await _safe_disconnect(client)
        raise _map_telethon_error(e)
    except (ConnectionError, OSError) as e:
        await _safe_disconnect(client)
        logger.error(f"[onboarding] proxy unreachable: {e}")
        raise HTTPException(
            status_code=502,
            detail={"code": "PROXY_UNAVAILABLE", "message": f"Proxy unreachable: {e}"},
        )
    except Exception as e:  # noqa: BLE001
        await _safe_disconnect(client)
        logger.error(f"[onboarding] start failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"code": "TELETHON_ERROR", "message": str(e)},
        )

    # D-17: capture session_string AFTER send_code_request — already carries
    # the DC routing needed to resume the flow on api-container restart.
    session_string = client.session.save()
    session_id = await save_state(
        db,
        workspace_id=ctx.workspace_id,
        phone=phone,
        phone_code_hash=sent_code.phone_code_hash,
        session_string=session_string,
        role=role,
        proxy=proxy,
    )
    _in_process_clients[str(session_id)] = client

    logger.info(
        f"[onboarding] started session={str(session_id)[:8]} "
        f"phone={phone[:6]}*** role={role} workspace={str(ctx.workspace_id)[:8]}"
    )
    return StartResponse(session_id=session_id, status="code_sent", phone=phone)


# ─── POST /onboarding/verify-code ────────────────────────────────────────────


@router.post("/verify-code")
async def verify_code(
    request: VerifyCodeRequest,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Verify SMS code. Creates sender on success or returns ``2fa_required``."""
    session_row = await load_state(db, request.session_id, ctx.workspace_id)
    if session_row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "SESSION_NOT_FOUND",
                "message": "Onboarding session not found or expired",
            },
        )
    if session_row.status != "code_sent":
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_STATE",
                "message": f"Session in state '{session_row.status}'",
            },
        )

    # Allow role override at verify-time (user changed their mind in UI).
    if (
        request.role
        and request.role in ("sender", "checker")
        and request.role != session_row.role
    ):
        await db.execute(
            text("UPDATE onboarding_sessions SET role = :r WHERE id = :sid"),
            {"r": request.role, "sid": str(session_row.id)},
        )
        await db.commit()
        session_row.role = request.role

    client = await _get_or_recover_client(session_row)

    try:
        await client.sign_in(
            phone=session_row.phone,
            code=request.code,
            phone_code_hash=session_row.phone_code_hash,
        )
    except SessionPasswordNeededError:
        new_session = encrypt_session(client.session.save())
        await update_status(
            db,
            session_row.id,
            "awaiting_2fa",
            encrypted_session_string=new_session,
        )
        logger.info(
            f"[onboarding] 2FA required session={str(session_row.id)[:8]}"
        )
        return {"status": "2fa_required", "session_id": str(session_row.id)}
    except (
        PhoneCodeInvalidError,
        PhoneCodeExpiredError,
        PhoneNumberBannedError,
        FloodWaitError,
    ) as e:
        raise _map_telethon_error(e)
    except Exception as e:  # noqa: BLE001
        logger.error(f"[onboarding] verify_code error: {e}", exc_info=True)
        await update_status(db, session_row.id, "failed")
        raise HTTPException(
            status_code=500,
            detail={"code": "TELETHON_ERROR", "message": str(e)},
        )

    sender = await _create_sender_from_session(
        db, ctx, session_row, client, name=request.name
    )
    await update_status(db, session_row.id, "completed")
    await _safe_disconnect(client)
    _drop_in_process(session_row.id)
    await delete_session(db, session_row.id)
    return {
        "status": "success",
        "sender_id": str(sender.id),
        "slug": sender.slug,
    }


# ─── POST /onboarding/verify-2fa ─────────────────────────────────────────────


@router.post("/verify-2fa")
async def verify_2fa(
    request: Verify2FARequest,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Submit 2FA password after verify-code returned ``2fa_required``."""
    session_row = await load_state(db, request.session_id, ctx.workspace_id)
    if session_row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "SESSION_NOT_FOUND",
                "message": "Onboarding session not found or expired",
            },
        )
    if session_row.status != "awaiting_2fa":
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_STATE",
                "message": f"Session in state '{session_row.status}'",
            },
        )

    client = await _get_or_recover_client(session_row)
    try:
        await client.sign_in(password=request.password)
    except (PasswordHashInvalidError, FloodWaitError) as e:
        raise _map_telethon_error(e)
    except Exception as e:  # noqa: BLE001
        logger.error(f"[onboarding] verify_2fa error: {e}", exc_info=True)
        await update_status(db, session_row.id, "failed")
        raise HTTPException(
            status_code=500,
            detail={"code": "TELETHON_ERROR", "message": str(e)},
        )

    sender = await _create_sender_from_session(
        db, ctx, session_row, client, name=request.name
    )
    await update_status(db, session_row.id, "completed")
    await _safe_disconnect(client)
    _drop_in_process(session_row.id)
    await delete_session(db, session_row.id)
    return {
        "status": "success",
        "sender_id": str(sender.id),
        "slug": sender.slug,
    }


# ─── POST /onboarding/qr-start ───────────────────────────────────────────────


@router.post("/qr-start")
async def qr_start(
    request: QrStartRequest,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Start QR login flow; the user scans the QR in Telegram mobile."""
    role = request.role or "sender"
    if role not in ("sender", "checker"):
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_ROLE", "message": f"Invalid role: {role}"},
        )
    proxy = await _resolve_proxy(db, ctx.workspace_id, request.proxy_id)

    client = make_telegram_client(StringSession(), proxy=proxy)
    try:
        await client.connect()
        qr_login = await client.qr_login()
    except (ConnectionError, OSError) as e:
        await _safe_disconnect(client)
        logger.error(f"[onboarding] QR proxy unreachable: {e}")
        raise HTTPException(
            status_code=502,
            detail={"code": "PROXY_UNAVAILABLE", "message": f"Proxy unreachable: {e}"},
        )
    except Exception as e:  # noqa: BLE001
        await _safe_disconnect(client)
        logger.error(f"[onboarding] qr_start failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"code": "TELETHON_ERROR", "message": str(e)},
        )

    # QR flow has no phone_code_hash; we store empty string and session_string
    # so that QR recovery state is at least visible to the cleanup worker.
    session_string = client.session.save()
    session_id = await save_state(
        db,
        workspace_id=ctx.workspace_id,
        phone="",
        phone_code_hash="",
        session_string=session_string,
        role=role,
        proxy=proxy,
    )
    _in_process_clients[str(session_id)] = client

    asyncio.create_task(
        _wait_for_qr(session_id, client, ctx.workspace_id, qr_login)
    )

    return {
        "session_id": str(session_id),
        "qr_image": _make_qr_image(qr_login.url),
        "status": "pending",
        "expires_in": 120,
    }


async def _wait_for_qr(
    session_id: UUID,
    client: TelegramClient,
    workspace_id: UUID,
    qr_login,
):
    """Background task — waits for the user to scan the QR (D-17 QR caveat)."""
    try:
        try:
            await qr_login.wait(timeout=120)
        except SessionPasswordNeededError:
            async with AsyncSessionLocal() as db:
                await update_status(
                    db,
                    session_id,
                    "awaiting_2fa",
                    encrypted_session_string=encrypt_session(client.session.save()),
                )
            return

        # Sign-in success — create sender, then mark session completed.
        async with AsyncSessionLocal() as db:
            row = (
                await db.execute(
                    select(OnboardingSession).where(
                        OnboardingSession.id == session_id,
                    )
                )
            ).scalars().first()
            if row is None:
                return
            # Mini-AuthCtx stand-in (workspace_id is all _create_sender_from_session
            # actually reads).
            class _Ctx:
                def __init__(self, wid: UUID):
                    self.workspace_id = wid

            await _create_sender_from_session(db, _Ctx(workspace_id), row, client)
            await update_status(db, session_id, "completed")
            await delete_session(db, session_id)
    except Exception as e:  # noqa: BLE001
        logger.error(f"[onboarding] _wait_for_qr error: {e}", exc_info=True)
        async with AsyncSessionLocal() as db:
            try:
                await update_status(db, session_id, "failed")
            except Exception:  # noqa: BLE001
                pass
    finally:
        _drop_in_process(session_id)
        await _safe_disconnect(client)


# ─── GET /onboarding/qr-status/{session_id} ──────────────────────────────────


@router.get("/qr-status/{session_id}")
async def qr_status(
    session_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Poll the QR flow status. Returns 'completed_or_expired' if the row is
    already deleted (success path) — UI uses GET /senders to confirm creation.
    """
    session_row = await load_state(db, session_id, ctx.workspace_id)
    if session_row is None:
        # Row may already be deleted (success), expired, or never existed.
        raw = await db.execute(
            select(OnboardingSession).where(
                OnboardingSession.id == session_id,
                OnboardingSession.workspace_id == ctx.workspace_id,
                # TODO(v2-rls): replaced by RLS policy
            )
        )
        row = raw.scalars().first()
        if row is None:
            return {"status": "completed_or_expired"}
        return {"status": row.status}
    return {"status": session_row.status}


# ─── DELETE /onboarding/cancel/{session_id} ──────────────────────────────────


@router.delete("/cancel/{session_id}", status_code=204)
async def cancel_onboarding(
    session_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Idempotent cancel — disconnect client, delete row, return 204."""
    session_row = await load_state(db, session_id, ctx.workspace_id)
    if session_row is None:
        # Idempotent — already cancelled / expired / never existed.
        return None
    client = _in_process_clients.get(str(session_id))
    await _safe_disconnect(client)
    _drop_in_process(session_id)
    await delete_session(db, session_id)
    return None


# ─── POST /onboarding/reauth/{sender_slug} ───────────────────────────────────


async def _load_sender_for_reauth(
    db: AsyncSession, ctx: AuthCtx, sender_slug: str
) -> Sender:
    result = await db.execute(
        select(Sender).where(
            Sender.slug == sender_slug,
            Sender.workspace_id == ctx.workspace_id,
            # TODO(v2-rls): replaced by RLS policy
        )
    )
    sender = result.scalars().first()
    if sender is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "SENDER_NOT_FOUND", "message": "Sender not found"},
        )
    return sender


@router.post("/reauth/{sender_slug}", response_model=StartResponse)
async def reauth_start(
    sender_slug: str,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Re-auth an existing sender via phone-code flow."""
    sender = await _load_sender_for_reauth(db, ctx, sender_slug)
    phone = _normalize_phone(sender.phone)
    if phone is None:
        raise HTTPException(
            status_code=400,
            detail={"code": "PHONE_INVALID", "message": "Sender has no valid phone"},
        )

    client = make_telegram_client(StringSession(), proxy=sender.proxy)
    try:
        await client.connect()
        sent_code = await client.send_code_request(phone)
    except (
        PhoneNumberInvalidError,
        PhoneNumberBannedError,
        FloodWaitError,
    ) as e:
        await _safe_disconnect(client)
        raise _map_telethon_error(e)
    except (ConnectionError, OSError) as e:
        await _safe_disconnect(client)
        raise HTTPException(
            status_code=502,
            detail={"code": "PROXY_UNAVAILABLE", "message": f"Proxy unreachable: {e}"},
        )
    except Exception as e:  # noqa: BLE001
        await _safe_disconnect(client)
        logger.error(f"[onboarding] reauth start failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"code": "TELETHON_ERROR", "message": str(e)},
        )

    session_string = client.session.save()
    session_id = await save_state(
        db,
        workspace_id=ctx.workspace_id,
        phone=phone,
        phone_code_hash=sent_code.phone_code_hash,
        session_string=session_string,
        role=sender.role,
        proxy=sender.proxy,
    )
    _in_process_clients[str(session_id)] = client
    logger.info(
        f"[onboarding] reauth started session={str(session_id)[:8]} "
        f"sender_slug={sender_slug}"
    )
    return StartResponse(session_id=session_id, status="code_sent", phone=phone)


# ─── POST /onboarding/reauth/qr/{sender_slug} ────────────────────────────────


@router.post("/reauth/qr/{sender_slug}")
async def reauth_qr(
    sender_slug: str,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Re-auth an existing sender via QR flow."""
    sender = await _load_sender_for_reauth(db, ctx, sender_slug)

    client = make_telegram_client(StringSession(), proxy=sender.proxy)
    try:
        await client.connect()
        qr_login = await client.qr_login()
    except (ConnectionError, OSError) as e:
        await _safe_disconnect(client)
        raise HTTPException(
            status_code=502,
            detail={"code": "PROXY_UNAVAILABLE", "message": f"Proxy unreachable: {e}"},
        )
    except Exception as e:  # noqa: BLE001
        await _safe_disconnect(client)
        raise HTTPException(
            status_code=500,
            detail={"code": "TELETHON_ERROR", "message": str(e)},
        )

    session_string = client.session.save()
    session_id = await save_state(
        db,
        workspace_id=ctx.workspace_id,
        phone=sender.phone or "",
        phone_code_hash="",
        session_string=session_string,
        role=sender.role,
        proxy=sender.proxy,
    )
    _in_process_clients[str(session_id)] = client
    asyncio.create_task(
        _wait_for_qr(session_id, client, ctx.workspace_id, qr_login)
    )

    return {
        "session_id": str(session_id),
        "qr_image": _make_qr_image(qr_login.url),
        "status": "pending",
        "expires_in": 120,
    }
