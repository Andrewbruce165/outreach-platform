"""
Senders router (Phase 2 — SNDR-01, SNDR-02, SNDR-03).

Все endpoint'ы workspace-scoped через `Depends(auth_dep)` (Phase 1 D-12 AuthCtx).

Endpoints:
  GET    /api/v1/senders                       — list workspace senders + derived status
  GET    /api/v1/senders/{slug}                — single sender by slug
  POST   /api/v1/senders                       — create (returns SenderCreateResponse с warnings[])
  PATCH  /api/v1/senders/{slug}                — update + warnings[] (D-14 soft cap)
  DELETE /api/v1/senders/{slug}                — cascade delete (контакты/диалоги/логи)
  POST   /api/v1/senders/{slug}/assign-proxy   — назначить прокси из workspace pool (D-22)
  GET    /api/v1/senders/{slug}/spambot-check  — SpamBot status через @SpamBot
  GET    /api/v1/workspace/proxies             — list workspace proxies (D-22)
  POST   /api/v1/workspace/proxies             — добавить прокси в pool
  DELETE /api/v1/workspace/proxies/{proxy_id}  — удалить из pool

Phase 2 ключевые изменения относительно legacy:
- Удалён docker-restart workaround (D-18). Reconcile делает listener сам
  (план 02-01 / Phase 2 W1).
- Удалена зависимость от старой legacy-auth: теперь Depends(auth_dep).
- is_active колонка дропнута в миграции 013 — derived status (D-11).
- Hard cap на rate_per_*: 10/50/300 (D-14) → 422 RATE_LIMIT_EXCEEDS_HARD_CAP.
- Soft cap (зелёный коридор): 4/20/150 → 200 + warnings[] (D-14).
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Sender, ProxyPool, SenderRestrictionEvent
from app.schemas import (
    AssignProxyRequest,
    ProxyConfig,
    ProxyPoolCreate,
    ProxyPoolItem,
    ProxyPoolListResponse,
    RateLimits,
    RestrictionEventResponse,
    SenderCreate,
    SenderCreateResponse,
    SenderListResponse,
    SenderResponse,
    SenderUpdate,
    WarningItem,
)
from app.services.encryption import encrypt_session
from app.utils.auth import AuthCtx, auth_dep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["senders"])

# D-14: hard cap = "exceeds maximum safe limit" → 422.
RATE_HARD_CAP = {"rate_per_min": 10, "rate_per_hour": 50, "rate_per_day": 300}
# D-14: soft cap = "зелёный коридор" → 200 + warnings[].
RATE_SOFT_CAP = {"rate_per_min": 4, "rate_per_hour": 20, "rate_per_day": 150}


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _derive_status(sender: Sender) -> str:
    """D-11 + migration 028: derived status — error > frozen > limited > lifecycle.

    Precedence:
      auth_status != 'ok' (session_expired / banned / etc) → 'error'
      restriction_status == 'frozen'                       → 'frozen'
      restriction_status == 'spam_limited'                 → 'limited'
      else                                                 → lifecycle_status

    Restriction is orthogonal to auth (a restricted account still authenticates),
    so it is checked only after auth is confirmed healthy.
    """
    if sender.auth_status != "ok":
        return "error"
    if sender.restriction_status == "frozen":
        return "frozen"
    if sender.restriction_status == "spam_limited":
        return "limited"
    return sender.lifecycle_status


def _sender_to_response(
    sender: Sender,
    sent_today: int = 0,
    locked_by_campaign_id: Optional[UUID] = None,
    locked_by_campaign_name: Optional[str] = None,
) -> SenderResponse:
    """Build SenderResponse с derived status + nested RateLimits.

    Phase 3 C-05: ai_context_id / ai_context_name fields removed — sender
    больше не «знает» агента, связь через Campaign в Phase 4.

    `sent_today` — trailing-24h sent count (TODAY column numerator). Computed
    only on the list endpoint; other paths use the default 0.

    `locked_by_campaign_id` / `locked_by_campaign_name` (POOL-09, 08-04 UAT) —
    the first running campaign in the workspace holding this sender, so the pool
    add-picker can disable locked entries instead of returning a confusing 409.
    Populated only on the list endpoint; single-sender paths report no lock
    (same convention as sent_today=0).
    """
    return SenderResponse(
        id=sender.id,
        slug=sender.slug,
        name=sender.name,
        phone=sender.phone,
        status=_derive_status(sender),
        auth_status=sender.auth_status,
        lifecycle_status=sender.lifecycle_status,
        restriction_status=sender.restriction_status,
        restricted_until=sender.restricted_until,
        rate_limits=RateLimits(
            per_minute=sender.rate_per_min,
            per_hour=sender.rate_per_hour,
            per_day=sender.rate_per_day,
        ),
        role=sender.role,
        proxy=ProxyConfig(**sender.proxy) if sender.proxy else None,
        last_used_at=sender.last_used_at,
        created_at=sender.created_at,
        sent_today=sent_today,
        locked_by_campaign_id=locked_by_campaign_id,
        locked_by_campaign_name=locked_by_campaign_name,
    )


def _validate_rate_limits(
    rate_per_min: Optional[int],
    rate_per_hour: Optional[int],
    rate_per_day: Optional[int],
) -> List[WarningItem]:
    """D-14: hard cap → 422; soft cap → warnings[].

    Pydantic уже отрезает hard cap через Field(le=...), но Lovable иногда шлёт
    через сырой JSON — двойная проверка тут не вредна и даёт более ясное сообщение.
    """
    warnings: List[WarningItem] = []
    values = {
        "rate_per_min": rate_per_min,
        "rate_per_hour": rate_per_hour,
        "rate_per_day": rate_per_day,
    }
    for field, val in values.items():
        if val is None:
            continue
        if val > RATE_HARD_CAP[field]:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "RATE_LIMIT_EXCEEDS_HARD_CAP",
                    "field": field,
                    "value": val,
                    "hard_cap": RATE_HARD_CAP[field],
                    "message": (
                        "exceeds maximum safe limit, contact support "
                        "if you need higher"
                    ),
                },
            )
        if val > RATE_SOFT_CAP[field]:
            warnings.append(
                WarningItem(
                    field=field,
                    value=val,
                    recommended_max=RATE_SOFT_CAP[field],
                )
            )
    return warnings


async def _check_sender_not_in_running_campaign(
    db: AsyncSession, ctx: AuthCtx, sender_id: UUID
) -> None:
    """Phase 4 close (new check, не TODO marker): sender нельзя удалить или
    pause/warmup-flip пока он прицеплен к running campaign в workspace.

    409 with detail{campaigns:[...]} — UX-friendly hint.
    """
    active = (await db.execute(text("""
        SELECT c.id, c.name
        FROM campaign_senders cs
        JOIN campaigns c ON c.id = cs.campaign_id
        WHERE cs.sender_id = :sid
          AND c.workspace_id = :wid
          AND c.status = 'running'
        ORDER BY c.name
    """), {"sid": str(sender_id), "wid": str(ctx.workspace_id)})).fetchall()
    if active:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "SENDER_USED_BY_RUNNING_CAMPAIGN",
                "message": (
                    "Sender is attached to running campaign(s) — "
                    "pause / finish them first"
                ),
                "campaigns": [{"id": str(r[0]), "name": r[1]} for r in active],
            },
        )


async def _load_sender_by_slug(
    db: AsyncSession, ctx: AuthCtx, slug: str
) -> Sender:
    """Workspace-scoped SELECT по slug.

    Phase 3 C-05: больше не selectinload(Sender.ai_context) — relationship
    дропнут вместе с senders.ai_context_id колонкой (D-04).

    404 без раскрытия "not yours" vs "not found" (security: same response
    как и в Phase 1 workspace.py).
    """
    result = await db.execute(
        select(Sender)
        .where(
            Sender.slug == slug,
            Sender.workspace_id == ctx.workspace_id,
            # TODO(v2-rls): replaced by RLS policy app.workspace_id
        )
    )
    sender = result.scalar_one_or_none()
    if sender is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "SENDER_NOT_FOUND", "message": f"Sender '{slug}' not found"},
        )
    return sender


# ─── Senders CRUD ────────────────────────────────────────────────────────────


@router.get("/senders", response_model=SenderListResponse)
async def list_senders(
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """List all senders in current workspace."""
    # Phase 3 C-05: selectinload(Sender.ai_context) удалён — relationship дропнут.
    result = await db.execute(
        select(Sender)
        .where(Sender.workspace_id == ctx.workspace_id)
        # TODO(v2-rls): replaced by RLS policy
        .order_by(Sender.name)
    )
    senders = result.scalars().all()

    # TODAY column numerator: messages sent per sender in the trailing 24h.
    # Single GROUP BY over message_queue (no N+1), scoped to the senders just
    # loaded. Window + status match the rate-limiter daily cap (queue.py:450-466)
    # so {sent_today}/{rate_per_day} never desyncs (no "151/150").
    sent_today_map: dict = {}
    sender_ids = [s.id for s in senders]
    if sender_ids:
        rows = (await db.execute(
            text("""
                SELECT sender_id, COUNT(*) AS sent_today
                  FROM message_queue
                 WHERE sender_id = ANY(:sender_ids)
                   AND status = 'sent'
                   AND finished_at >= now() - interval '24 hours'
                 GROUP BY sender_id
            """),
            {"sender_ids": sender_ids},
        )).fetchall()
        sent_today_map = {row[0]: row[1] for row in rows}

    # POOL-09 (08-04 UAT fix): per-sender lock state for the pool add-picker.
    # One grouped query (no N+1, like sent_today_map above), workspace-scoped,
    # with semantics IDENTICAL to _check_sender_not_in_running_campaign (sender
    # attached to a campaign with status='running' in this workspace). DISTINCT ON
    # picks the first running campaign per sender deterministically (ORDER BY
    # c.name, mirroring the existing helper) so the disabled-pill tooltip is
    # stable. None here = the sender is free to attach.
    lock_map: dict = {}
    if sender_ids:
        lock_rows = (await db.execute(
            text("""
                SELECT DISTINCT ON (cs.sender_id) cs.sender_id, c.id, c.name
                  FROM campaign_senders cs
                  JOIN campaigns c ON c.id = cs.campaign_id
                 WHERE cs.sender_id = ANY(:sender_ids)
                   AND c.workspace_id = :wid
                   AND c.status = 'running'
                 ORDER BY cs.sender_id, c.name
            """),
            {"sender_ids": sender_ids, "wid": str(ctx.workspace_id)},
        )).fetchall()
        lock_map = {row[0]: (row[1], row[2]) for row in lock_rows}

    return SenderListResponse(
        senders=[
            _sender_to_response(
                s,
                sent_today=sent_today_map.get(s.id, 0),
                locked_by_campaign_id=lock_map.get(s.id, (None, None))[0],
                locked_by_campaign_name=lock_map.get(s.id, (None, None))[1],
            )
            for s in senders
        ]
    )


@router.post("/senders", response_model=SenderCreateResponse, status_code=201)
async def create_sender(
    request: SenderCreate,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Add new sender.

    Slug — globally unique (Phase 1 inheritance). Возвращает sender + warnings[]
    если rate_per_* выше soft cap.
    """
    # Globally unique slug check.
    existing = await db.execute(select(Sender).where(Sender.slug == request.slug))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail={
                "code": "SLUG_EXISTS",
                "message": f"Sender with slug '{request.slug}' already exists",
            },
        )

    warnings = _validate_rate_limits(
        request.rate_per_min, request.rate_per_hour, request.rate_per_day
    )

    encrypted_session = encrypt_session(request.session_string)

    sender = Sender(
        workspace_id=ctx.workspace_id,
        slug=request.slug,
        name=request.name,
        phone=request.phone,
        session_string=encrypted_session,
        role=request.role or "sender",
        proxy=request.proxy.model_dump() if request.proxy else None,
        # Phase 3 C-05: ai_context_id removed from constructor — column dropped.
    )
    if request.rate_per_min is not None:
        sender.rate_per_min = request.rate_per_min
    if request.rate_per_hour is not None:
        sender.rate_per_hour = request.rate_per_hour
    if request.rate_per_day is not None:
        sender.rate_per_day = request.rate_per_day

    db.add(sender)
    await db.commit()
    await db.refresh(sender)

    # Phase 3 C-05: no longer reload with ai_context — relationship dropped.

    # Auto-assign free proxy from workspace pool (D-22). Skip if user passed proxy explicitly.
    if not sender.proxy:
        pool_result = await db.execute(
            select(ProxyPool)
            .where(
                ProxyPool.workspace_id == ctx.workspace_id,
                ProxyPool.assigned_to_sender_id.is_(None),
            )
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
            logger.info(
                f"[proxy-pool] auto-assigned port {pool_entry.port} → sender {sender.slug}"
            )
        else:
            logger.warning(
                f"[proxy-pool] no free proxy in workspace {ctx.workspace_id} "
                f"for sender {sender.slug}"
            )

    logger.info(
        f"[senders] created workspace={ctx.workspace_id} slug={sender.slug} "
        f"role={sender.role} warnings={len(warnings)}"
    )
    return SenderCreateResponse(sender=_sender_to_response(sender), warnings=warnings)


@router.get("/senders/{slug}", response_model=SenderResponse)
async def get_sender(
    slug: str,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Get sender by slug (workspace-scoped). Returns derived status."""
    sender = await _load_sender_by_slug(db, ctx, slug)
    return _sender_to_response(sender)


@router.patch("/senders/{slug}", response_model=SenderCreateResponse)
async def update_sender(
    slug: str,
    request: SenderUpdate,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Update sender. Returns warnings[] (D-14 soft cap)."""
    sender = await _load_sender_by_slug(db, ctx, slug)

    warnings = _validate_rate_limits(
        request.rate_per_min, request.rate_per_hour, request.rate_per_day
    )

    if request.name is not None:
        sender.name = request.name
    if request.phone is not None:
        sender.phone = request.phone
    if request.session_string is not None:
        sender.session_string = encrypt_session(request.session_string)
    if request.lifecycle_status is not None:
        # Phase 4 close: cannot pause/warmup-flip a sender that is currently in running campaign.
        if request.lifecycle_status != "active" and sender.lifecycle_status == "active":
            await _check_sender_not_in_running_campaign(db, ctx, sender.id)
        sender.lifecycle_status = request.lifecycle_status
    if request.rate_per_min is not None:
        sender.rate_per_min = request.rate_per_min
    if request.rate_per_hour is not None:
        sender.rate_per_hour = request.rate_per_hour
    if request.rate_per_day is not None:
        sender.rate_per_day = request.rate_per_day
    # Phase 3 C-05: ai_context_id setter removed — column dropped.
    if request.role is not None:
        sender.role = request.role
    if request.proxy is not None:
        sender.proxy = request.proxy.model_dump()

    await db.commit()
    await db.refresh(sender)

    # Phase 3 C-05: no longer reload with ai_context — relationship dropped.

    logger.info(
        f"[senders] updated workspace={ctx.workspace_id} slug={sender.slug} "
        f"lifecycle={sender.lifecycle_status} warnings={len(warnings)}"
    )
    return SenderCreateResponse(sender=_sender_to_response(sender), warnings=warnings)


@router.delete("/senders/{slug}", status_code=204)
async def delete_sender(
    slug: str,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Hard delete sender + связанные диалоги/сообщения/контакты.

    Phase 4 close: 409 if sender is attached to any running campaign in workspace.
    """
    sender = await _load_sender_by_slug(db, ctx, slug)

    # Phase 4 close: block delete if sender in running campaign.
    await _check_sender_not_in_running_campaign(db, ctx, sender.id)

    sender_id = str(sender.id)

    # CASCADE через FK не покрывает 'messages' (нет sender_id напрямую — через conversations).
    await db.execute(
        text(
            "DELETE FROM messages WHERE conversation_id IN "
            "(SELECT id FROM conversations WHERE sender_id = :sid)"
        ),
        {"sid": sender_id},
    )
    await db.execute(
        text("DELETE FROM conversations WHERE sender_id = :sid"),
        {"sid": sender_id},
    )
    await db.execute(
        text("DELETE FROM contacts_cache WHERE sender_id = :sid"),
        {"sid": sender_id},
    )
    await db.execute(
        text("DELETE FROM messages_log WHERE sender_id = :sid"),
        {"sid": sender_id},
    )

    await db.delete(sender)
    await db.commit()
    logger.info(f"[senders] deleted workspace={ctx.workspace_id} slug={slug}")


# ─── Lifecycle endpoints (Phase 05.1 — UI-ACCT-01) ───────────────────────────


@router.post("/senders/{slug}/pause", response_model=SenderCreateResponse)
async def pause_sender(
    slug: str,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """UI-SPEC §5.10 row action Pause — flip lifecycle_status active → paused.

    Phase 05.1 (UI-ACCT-01): explicit endpoint added alongside existing PATCH path
    so the UI doesn't need to send a typed body for a one-button action. The Phase 4
    sender-lock guard still applies — cannot pause a sender mid-campaign without
    explicit campaign teardown.

    Idempotent: already-paused → 200 with current state (matches frontend retry
    semantics on flaky network).
    """
    sender = await _load_sender_by_slug(db, ctx, slug)
    if sender.lifecycle_status == "paused":
        # Idempotent — return current state instead of erroring.
        return SenderCreateResponse(sender=_sender_to_response(sender), warnings=[])
    if sender.lifecycle_status != "active":
        raise HTTPException(
            status_code=409,
            detail={"code": "INVALID_TRANSITION",
                    "from": sender.lifecycle_status, "to": "paused"},
        )
    # Same guard PATCH uses (line 351): cannot pause if locked in running campaign.
    await _check_sender_not_in_running_campaign(db, ctx, sender.id)
    sender.lifecycle_status = "paused"
    await db.commit()
    await db.refresh(sender)
    logger.info(
        f"[senders] paused workspace={ctx.workspace_id} slug={sender.slug}"
    )
    return SenderCreateResponse(sender=_sender_to_response(sender), warnings=[])


@router.post("/senders/{slug}/resume", response_model=SenderCreateResponse)
async def resume_sender(
    slug: str,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """UI-SPEC §5.10 row action Resume — flip lifecycle_status paused → active.

    Idempotent: already-active → 200 with current state.
    """
    sender = await _load_sender_by_slug(db, ctx, slug)
    if sender.lifecycle_status == "active":
        return SenderCreateResponse(sender=_sender_to_response(sender), warnings=[])
    if sender.lifecycle_status != "paused":
        raise HTTPException(
            status_code=409,
            detail={"code": "INVALID_TRANSITION",
                    "from": sender.lifecycle_status, "to": "active"},
        )
    sender.lifecycle_status = "active"
    await db.commit()
    await db.refresh(sender)
    logger.info(
        f"[senders] resumed workspace={ctx.workspace_id} slug={sender.slug}"
    )
    return SenderCreateResponse(sender=_sender_to_response(sender), warnings=[])


# ─── Proxy assignment ────────────────────────────────────────────────────────


@router.post("/senders/{slug}/assign-proxy", response_model=SenderResponse)
async def assign_proxy(
    slug: str,
    request: AssignProxyRequest,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """D-22: назначить sender'у прокси из workspace pool.

    Записывает proxy JSON в sender.proxy и привязывает proxy_pool.assigned_to_sender_id.
    """
    sender = await _load_sender_by_slug(db, ctx, slug)

    # Workspace-scoped proxy lookup.
    proxy_result = await db.execute(
        select(ProxyPool).where(
            ProxyPool.id == request.proxy_id,
            ProxyPool.workspace_id == ctx.workspace_id,
            # TODO(v2-rls): replaced by RLS policy
        )
    )
    proxy_row = proxy_result.scalar_one_or_none()
    if proxy_row is None:
        # 404, не 403 (security: не раскрываем cross-tenant существование).
        raise HTTPException(
            status_code=404,
            detail={"code": "PROXY_NOT_FOUND", "message": "Proxy not found"},
        )

    sender.proxy = {
        "type": "socks5",
        "host": proxy_row.host,
        "port": proxy_row.port,
        "username": proxy_row.username,
        "password": proxy_row.password,
    }
    proxy_row.assigned_to_sender_id = sender.id
    await db.commit()
    await db.refresh(sender)

    # Phase 3 C-05: no longer reload with ai_context — relationship dropped.

    logger.info(
        f"[senders] assign-proxy workspace={ctx.workspace_id} slug={slug} "
        f"proxy_id={proxy_row.id} port={proxy_row.port}"
    )
    return _sender_to_response(sender)


# ─── SpamBot ─────────────────────────────────────────────────────────────────


@router.get("/senders/{slug}/spambot-check")
async def check_spambot(
    slug: str,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Check sender's account status via @SpamBot.

    Returns:
        status: 'free' | 'limited' | 'suspended' | 'unknown'
        raw_text: full SpamBot response
        auth_status_updated: новый auth_status (если поменялся)

    Phase 2 (D-11): больше не пишет is_active=False — derived 'error' computed
    из auth_status при чтении.
    """
    from app.services.telegram import telegram_service, SessionAuthError

    sender = await _load_sender_by_slug(db, ctx, slug)
    if not sender.session_string:
        raise HTTPException(
            status_code=400,
            detail={"code": "NO_SESSION", "message": "Sender has no session"},
        )

    client = None
    try:
        client = await telegram_service.get_client(
            sender.slug, sender.session_string, proxy=sender.proxy
        )
        # selfcheck_key passed for intent/forward-compat. NOTE: this endpoint runs
        # in the api process; the SpamBot reply is handled by the listener's
        # persistent client in the listener process, where this in-memory marker is
        # NOT visible — so the antispam auto-cancel is not suppressed here (known
        # cross-process limitation, decided 2026-06-22).
        spambot_result = await telegram_service.check_spambot(client, selfcheck_key=sender.slug)

        # Migration 028: map SpamBot verdict onto the right column.
        #   free      → clear restriction (restriction_status='none')
        #   limited   → restriction_status='spam_limited' + recheck window
        #   suspended → real ban → auth_status='banned' (auth-level, not restriction)
        # Previously this wrote a bogus auth_status='limited' (not a valid enum value).
        from app.config import get_settings

        verdict = spambot_result["status"]
        if verdict == "suspended" and sender.auth_status != "banned":
            sender.auth_status = "banned"
            await db.commit()
            spambot_result["auth_status_updated"] = "banned"
        elif verdict == "limited":
            sender.restriction_status = "spam_limited"
            # Prefer SpamBot's quoted release time (+5min buffer); else fixed interval.
            iso = spambot_result.get("limit_until")
            recheck_at = datetime.now(timezone.utc) + timedelta(
                seconds=get_settings().restriction_recheck_interval_seconds
            )
            if iso:
                try:
                    candidate = datetime.fromisoformat(iso) + timedelta(minutes=5)
                    if candidate > datetime.now(timezone.utc):
                        recheck_at = candidate
                except ValueError:
                    pass
            sender.restricted_until = recheck_at
            await db.commit()
            spambot_result["restriction_status_updated"] = "spam_limited"
        elif verdict == "free" and sender.restriction_status != "none":
            sender.restriction_status = "none"
            sender.restricted_until = None
            await db.commit()
            spambot_result["restriction_status_updated"] = "none"

        return spambot_result
    except SessionAuthError as e:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "AUTH_ERROR",
                "message": f"Session auth failed: {e.auth_status}",
                "auth_status": e.auth_status,
            },
        )
    except Exception as e:
        logger.error(f"SpamBot check failed for {slug}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"code": "SPAMBOT_CHECK_FAILED", "message": str(e)},
        )
    finally:
        if client:
            await telegram_service.disconnect_client(client)


# ─── Restriction event history (HLTH-03) ─────────────────────────────────────


@router.get(
    "/senders/{slug}/restriction-events",
    response_model=list[RestrictionEventResponse],
)
async def list_restriction_events(
    slug: str,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """HLTH-03: append-only restriction-event history for a sender, newest-first.

    Workspace-scoped via _load_sender_by_slug (opaque 404 for foreign/unknown
    slugs — no cross-tenant leakage). Read-only over the append-only log; the
    event SELECT is also constrained by workspace_id (defence-in-depth) and
    bounded by a default LIMIT.
    """
    sender = await _load_sender_by_slug(db, ctx, slug)
    result = await db.execute(
        select(SenderRestrictionEvent)
        .where(
            SenderRestrictionEvent.sender_id == sender.id,
            SenderRestrictionEvent.workspace_id == ctx.workspace_id,
        )
        .order_by(SenderRestrictionEvent.created_at.desc())
        .limit(200)
    )
    return result.scalars().all()


# ─── Workspace proxy pool CRUD (D-22) ────────────────────────────────────────


@router.get("/workspace/proxies", response_model=ProxyPoolListResponse)
async def list_proxies(
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """List workspace proxy pool (D-22)."""
    result = await db.execute(
        select(ProxyPool)
        .where(ProxyPool.workspace_id == ctx.workspace_id)
        # TODO(v2-rls): replaced by RLS policy
        .order_by(ProxyPool.created_at.desc())
    )
    proxies = result.scalars().all()
    items = [
        ProxyPoolItem(
            id=p.id,
            host=p.host,
            port=p.port,
            type="socks5",
            username=p.username,
            assigned_to_sender_id=p.assigned_to_sender_id,
        )
        for p in proxies
    ]
    return ProxyPoolListResponse(proxies=items, total=len(items))


@router.post("/workspace/proxies", response_model=ProxyPoolItem, status_code=201)
async def create_proxy(
    request: ProxyPoolCreate,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Add proxy to workspace pool (D-22)."""
    proxy = ProxyPool(
        workspace_id=ctx.workspace_id,
        host=request.host,
        port=request.port,
        username=request.username or "",
        password=request.password,
    )
    db.add(proxy)
    await db.commit()
    await db.refresh(proxy)

    logger.info(
        f"[proxy-pool] created workspace={ctx.workspace_id} "
        f"host={proxy.host} port={proxy.port}"
    )
    return ProxyPoolItem(
        id=proxy.id,
        host=proxy.host,
        port=proxy.port,
        type=request.type,
        username=proxy.username,
        assigned_to_sender_id=proxy.assigned_to_sender_id,
    )


@router.delete("/workspace/proxies/{proxy_id}", status_code=204)
async def delete_proxy(
    proxy_id: UUID,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Remove proxy from workspace pool (D-22)."""
    result = await db.execute(
        select(ProxyPool).where(
            ProxyPool.id == proxy_id,
            ProxyPool.workspace_id == ctx.workspace_id,
            # TODO(v2-rls): replaced by RLS policy
        )
    )
    proxy = result.scalar_one_or_none()
    if proxy is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "PROXY_NOT_FOUND", "message": "Proxy not found"},
        )
    await db.delete(proxy)
    await db.commit()
    logger.info(
        f"[proxy-pool] deleted workspace={ctx.workspace_id} proxy_id={proxy_id}"
    )
