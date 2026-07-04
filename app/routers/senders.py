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
import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Sender, ProxyPool, SenderRestrictionEvent
from app.schemas import (
    AssignProxyRequest,
    ProfileUpdate,
    ProfileUpdateResponse,
    ProfileWarningItem,
    ProxyConfig,
    ProxyPoolCreate,
    ProxyPoolItem,
    ProxyPoolListResponse,
    RateLimits,
    RestrictionEventResponse,
    SenderBlockRateResponse,
    SenderCreate,
    SenderCreateResponse,
    SenderListResponse,
    SenderResponse,
    SenderUpdate,
    UsernameCheckResponse,
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


def _derive_checker_status(sender: Sender) -> Optional[str]:
    """Checker-specific UI status (role='checker' only; None for senders).

    Distinct from _derive_status (sender-oriented). Splits the generic
    'error'/'limited' into action-vs-auto buckets so the UI can show
    'Re-auth needed'/'Banned' (red, needs the user) separately from
    'Cooling down' (amber, auto-recovering contacts-API throttle — no action).

    Precedence: banned > reauth_needed > frozen > cooling_down > paused > active.
    """
    if sender.role != "checker":
        return None
    if sender.auth_status == "banned":
        return "banned"
    if sender.auth_status != "ok":
        return "reauth_needed"
    if sender.restriction_status == "frozen":
        return "frozen"
    if sender.restriction_status == "spam_limited":
        return "cooling_down"
    if sender.lifecycle_status == "paused":
        return "paused"
    return "active"


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
        checker_status=_derive_checker_status(sender),
        checker_trip_count=getattr(sender, "checker_trip_count", 0) or 0,
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
        # Phase 20 (PROF-01/02/03/07 + D-08): cached Telegram profile fields.
        tg_username=getattr(sender, "tg_username", None),
        tg_bio=getattr(sender, "tg_bio", None),
        has_photo=bool(getattr(sender, "tg_photo", None)),
        profile_field_changed_at=getattr(sender, "profile_field_changed_at", {}) or {},
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


# ─── Account profile guardrail (Phase 20 — D-06/D-07/D-08/D-09) ───────────────

# Telegram username rule: 5–32 chars, must start with a letter, only [A-Za-z0-9_].
_USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")
# Free-account bio cap; AboutTooLongError is the premium (140) backstop.
_BIO_MAX_LEN = 70
# D-08: ONLY these fields hard-block when changed <1h ago. name/bio are warning-only (D-07).
_HARD_BLOCK_FIELDS = {"username", "photo"}
_HARD_BLOCK_WINDOW = timedelta(hours=1)

# PROF-04 photo upload guards — validated BEFORE any Telegram call (413/422).
MAX_PHOTO_BYTES = 5 * 1024 * 1024
ALLOWED_PHOTO_MIME = {"image/jpeg", "image/png"}


def _as_aware(dt: Optional[datetime]) -> Optional[datetime]:
    """Normalise a possibly-naive datetime to UTC-aware for safe arithmetic."""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _stamp_profile_change(sender: Sender, field: str) -> None:
    """Record the last-change time for a field. Reassign a NEW dict — SQLAlchemy does
    not track in-place JSONB mutation (no MutableDict), so mutating in place would not
    persist on commit."""
    changed = dict(sender.profile_field_changed_at or {})
    changed[field] = datetime.now(timezone.utc).isoformat()
    sender.profile_field_changed_at = changed


def _check_profile_cooldown(sender: Sender, field: str) -> None:
    """D-08 HARD block: username/photo changed <1h ago → 409 TOO_FREQUENT.

    name/bio are warning-only (D-07) → not in _HARD_BLOCK_FIELDS → no-op here.
    """
    if field not in _HARD_BLOCK_FIELDS:
        return
    ts = (sender.profile_field_changed_at or {}).get(field)
    if not ts:
        return
    try:
        last = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return
    last = _as_aware(last)
    elapsed = datetime.now(timezone.utc) - last
    if elapsed < _HARD_BLOCK_WINDOW:
        retry_after = int((_HARD_BLOCK_WINDOW - elapsed).total_seconds())
        raise HTTPException(
            status_code=409,
            detail={
                "code": "TOO_FREQUENT",
                "message": (
                    f"{field} можно менять не чаще раза в час. "
                    f"Попробуйте снова через {retry_after} c."
                ),
                "retry_after": retry_after,
                "retry_after_seconds": retry_after,
                "field": field,
            },
        )


def _profile_advisory(sender: Sender) -> List[ProfileWarningItem]:
    """D-09 advisory (NEVER blocks): warmup OR account younger than 7 days.

    Returns ProfileWarningItem (code/message) — NOT the rate-limit WarningItem (D-14).
    """
    warnings: List[ProfileWarningItem] = []
    created = _as_aware(getattr(sender, "created_at", None))
    young = created is not None and (datetime.now(timezone.utc) - created) < timedelta(days=7)
    if sender.lifecycle_status == "warmup" or young:
        warnings.append(
            ProfileWarningItem(
                code="PROFILE_WARMUP_ADVISORY",
                message=(
                    "Аккаунт ещё прогревается (моложе 7 дней). Резкие изменения "
                    "профиля повышают риск ограничений."
                ),
            )
        )
    return warnings


def _raise_profile_telegram_error(e: Exception) -> None:
    """Map a Telegram/Telethon profile error to a structured HTTPException.

    Matches on BOTH the exception class name and its message text, so the live
    Telethon errors (UsernameOccupiedError, AboutTooLongError, FloodWaitError, ...)
    and a test that raises a bare Exception("USERNAME_OCCUPIED") both resolve to the
    same code. Unknown errors → 500 PROFILE_UPDATE_FAILED.
    """
    blob = f"{type(e).__name__} {e}".upper()
    table = (
        ("USERNAME_OCCUPIED", 400, "USERNAME_TAKEN", "Этот username уже занят"),
        ("USERNAMEOCCUPIED", 400, "USERNAME_TAKEN", "Этот username уже занят"),
        ("USERNAME_INVALID", 400, "USERNAME_INVALID",
         "Недопустимый формат username (a-z, 0-9, _, 5–32 символа)"),
        ("USERNAMEINVALID", 400, "USERNAME_INVALID",
         "Недопустимый формат username (a-z, 0-9, _, 5–32 символа)"),
        ("USERNAME_PURCHASE", 400, "USERNAME_PURCHASE_REQUIRED",
         "Этот username платный (Fragment)"),
        ("USERNAMEPURCHASE", 400, "USERNAME_PURCHASE_REQUIRED",
         "Этот username платный (Fragment)"),
        ("ABOUT_TOO_LONG", 400, "BIO_TOO_LONG",
         f"Описание слишком длинное (максимум {_BIO_MAX_LEN} символов)"),
        ("ABOUTTOOLONG", 400, "BIO_TOO_LONG",
         f"Описание слишком длинное (максимум {_BIO_MAX_LEN} символов)"),
        ("FIRSTNAME_INVALID", 400, "NAME_INVALID", "Недопустимое имя"),
        ("FIRSTNAMEINVALID", 400, "NAME_INVALID", "Недопустимое имя"),
        ("PHOTO_CROP_SIZE_SMALL", 400, "PHOTO_TOO_SMALL", "Фото слишком маленькое"),
        ("PHOTOCROPSIZESMALL", 400, "PHOTO_TOO_SMALL", "Фото слишком маленькое"),
        ("PHOTO_EXT_INVALID", 400, "PHOTO_FORMAT_INVALID",
         "Неподдерживаемый формат. Загрузите JPG или PNG"),
        ("PHOTOEXTINVALID", 400, "PHOTO_FORMAT_INVALID",
         "Неподдерживаемый формат. Загрузите JPG или PNG"),
        ("FLOOD_WAIT", 429, "FLOOD_WAIT", "Слишком часто. Попробуйте позже."),
        ("FLOODWAIT", 429, "FLOOD_WAIT", "Слишком часто. Попробуйте позже."),
    )
    for needle, status, code, message in table:
        if needle in blob:
            detail = {"code": code, "message": message}
            if code == "FLOOD_WAIT":
                secs = getattr(e, "seconds", None)
                if secs is not None:
                    detail["retry_after"] = secs
            raise HTTPException(status_code=status, detail=detail)
    logger.error(f"[senders] profile telegram error: {type(e).__name__}: {e}")
    raise HTTPException(
        status_code=500,
        detail={"code": "PROFILE_UPDATE_FAILED", "message": str(e)},
    )


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


# ─── Block-rate aggregate (SRLD-08, D-15/D-16) ───────────────────────────────


@router.get(
    "/senders/{slug}/block-rate",
    response_model=SenderBlockRateResponse,
)
async def get_block_rate(
    slug: str,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """SRLD-08 (D-15/D-16): read-only per-sender block-rate over a 7-day window.

    Counts durable event_type='blocked' restriction events vs message_type='sent'
    messages_log rows. block_rate = blocks_7d / sends_7d (0.0 when no sends) — the
    design-doc "metric that actually matters" (blocks → reports → PeerFlood →
    freeze). STRICTLY read-only (D-16): NO control-loop, NO auto-pause, NO writes.

    Workspace-scoped via _load_sender_by_slug (opaque 404 for foreign/unknown
    slugs) PLUS an explicit workspace_id filter in the SQL (defence-in-depth,
    mirroring list_restriction_events).
    """
    sender = await _load_sender_by_slug(db, ctx, slug)
    row = (await db.execute(text("""
        SELECT
          (SELECT COUNT(*) FROM sender_restriction_events e
            WHERE e.sender_id = :sid AND e.workspace_id = :wid
              AND e.event_type = 'blocked'
              AND e.created_at > now() - interval '7 days')  AS blocks_7d,
          (SELECT COUNT(*) FROM messages_log m
            WHERE m.sender_id = :sid AND m.workspace_id = :wid
              AND m.message_type = 'sent'
              AND m.created_at > now() - interval '7 days')  AS sends_7d
    """), {"sid": str(sender.id), "wid": str(ctx.workspace_id)})).one()
    blocks_7d = int(row.blocks_7d)
    sends_7d = int(row.sends_7d)
    return SenderBlockRateResponse(
        blocks_7d=blocks_7d,
        sends_7d=sends_7d,
        block_rate=(blocks_7d / sends_7d) if sends_7d else 0.0,
    )


# ─── Account profile edit (Phase 20 — PROF-02/03, D-06/07/08/09) ─────────────


@router.get("/senders/{slug}/username-check", response_model=UsernameCheckResponse)
async def username_check(
    slug: str,
    username: str,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """Username availability pre-check (C5).

    1. Local format validation (Telegram rules) — invalid → available=False/'invalid'.
    2. Re-submitting the account's own current username → available (no-op).
    3. Best-effort live check via CheckUsernameRequest. If the session can't be reached
       we fall back to the format-valid verdict — the authoritative occupancy check runs
       at PATCH time (UpdateUsernameRequest → USERNAME_TAKEN).
    """
    from app.services.telegram import telegram_service

    sender = await _load_sender_by_slug(db, ctx, slug)

    if not username or not _USERNAME_RE.match(username):
        return UsernameCheckResponse(available=False, reason="invalid")

    if sender.tg_username and username.lower() == sender.tg_username.lower():
        return UsernameCheckResponse(available=True, reason=None)

    try:
        res = await telegram_service.check_username(
            sender.slug, sender.session_string, username, proxy=sender.proxy
        )
    except Exception as e:  # noqa: BLE001 — session unreachable → best-effort fall-through
        logger.info(f"[senders] username-check live probe failed for {slug}: {e}")
        return UsernameCheckResponse(available=True, reason=None)

    return UsernameCheckResponse(
        available=bool(res.get("available", True)), reason=res.get("reason")
    )


@router.patch("/senders/{slug}/profile", response_model=ProfileUpdateResponse)
async def update_sender_profile(
    slug: str,
    request: ProfileUpdate,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """PATCH Section-A identity: first/last name + bio (warning-only, D-07) and username
    (1h hard block, D-08). Order: bio-length guard → username cooldown → Telegram writes
    → cache refresh + stamp → commit → D-09 advisory warnings.
    """
    from telethon.tl.functions.account import UpdateProfileRequest
    from app.services.telegram import telegram_service, SessionAuthError

    sender = await _load_sender_by_slug(db, ctx, slug)

    # Bio length guard → 400 BIO_TOO_LONG (before any Telegram call).
    if request.about is not None and len(request.about) > _BIO_MAX_LEN:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "BIO_TOO_LONG",
                "message": f"Описание слишком длинное (максимум {_BIO_MAX_LEN} символов)",
            },
        )

    changing_profile = (
        request.first_name is not None
        or request.last_name is not None
        or request.about is not None
    )
    changing_username = request.username is not None

    # D-08 hard block for username BEFORE any Telegram call (photo has its own endpoint).
    if changing_username:
        _check_profile_cooldown(sender, "username")

    try:
        if changing_profile:
            # Only pass fields the user actually changed (None leaves them untouched).
            req = UpdateProfileRequest(
                first_name=request.first_name,
                last_name=request.last_name,
                about=request.about,
            )
            await telegram_service.update_profile(
                sender.slug, sender.session_string, req, proxy=sender.proxy
            )
            if request.first_name is not None:
                composed = (
                    (request.first_name or "")
                    + (" " + request.last_name if request.last_name else "")
                ).strip()
                sender.name = composed or sender.name
                _stamp_profile_change(sender, "name")
            if request.about is not None:
                sender.tg_bio = request.about
                _stamp_profile_change(sender, "bio")
        if changing_username:
            await telegram_service.update_username(
                sender.slug, sender.session_string, request.username, proxy=sender.proxy
            )
            sender.tg_username = request.username or None
            _stamp_profile_change(sender, "username")
    except SessionAuthError as e:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "AUTH_ERROR",
                "message": f"Session auth failed: {e.auth_status}",
                "auth_status": e.auth_status,
            },
        )
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — mapped to a structured HTTP error
        _raise_profile_telegram_error(e)

    await db.commit()
    await db.refresh(sender)
    return ProfileUpdateResponse(
        sender=_sender_to_response(sender), warnings=_profile_advisory(sender)
    )


# ─── Account profile photo (Phase 20 — PROF-04/07, D-08/D-11) ────────────────


@router.post("/senders/{slug}/photo", response_model=ProfileUpdateResponse)
async def upload_sender_photo(
    slug: str,
    file: UploadFile = File(...),
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """PROF-04: upload a new profile photo (multipart).

    Order: size/mime validation (413/422, BEFORE any Telegram call) → D-08 photo
    cooldown (409, BEFORE the Telegram write) → upload → cache Telegram's normalized
    avatar (falls back to the raw upload) → per-field stamp → commit → D-09 advisory.

    NB: validation runs before the cooldown so a bad upload always reports the input
    error, not a stale-cooldown 409.
    """
    from app.services.telegram import telegram_service, SessionAuthError

    sender = await _load_sender_by_slug(db, ctx, slug)

    raw = await file.read()
    if len(raw) > MAX_PHOTO_BYTES:
        raise HTTPException(
            status_code=413,
            detail={"code": "FILE_TOO_LARGE", "message": "Файл слишком большой (максимум 5 МБ)"},
        )
    if file.content_type not in ALLOWED_PHOTO_MIME:
        raise HTTPException(
            status_code=422,
            detail={"code": "UNSUPPORTED_FILE_TYPE", "message": "Только JPG или PNG"},
        )

    # D-08 hard block for photo BEFORE the Telegram write (after input validation).
    _check_profile_cooldown(sender, "photo")

    try:
        res = await telegram_service.upload_profile_photo(
            sender.slug,
            sender.session_string,
            raw,
            file_name=file.filename or "avatar.jpg",
            proxy=sender.proxy,
        )
    except SessionAuthError as e:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "AUTH_ERROR",
                "message": f"Session auth failed: {e.auth_status}",
                "auth_status": e.auth_status,
            },
        )
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — mapped to a structured HTTP error (incl. FLOOD_WAIT)
        _raise_profile_telegram_error(e)

    res = res or {}
    # Cache Telegram's own normalized avatar when the service returns it; otherwise
    # fall back to the raw upload bytes (D-11: bytes stay server-side, served via GET).
    sender.tg_photo = res.get("photo") or raw
    sender.tg_photo_mime = res.get("photo_mime") or file.content_type
    _stamp_profile_change(sender, "photo")
    await db.commit()
    await db.refresh(sender)
    return ProfileUpdateResponse(
        sender=_sender_to_response(sender), warnings=_profile_advisory(sender)
    )


@router.delete("/senders/{slug}/photo", response_model=ProfileUpdateResponse)
async def delete_sender_photo(
    slug: str,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """PROF-04: remove the profile photo and clear the cache.

    A delete is a de-escalation (removing, not spamming a fresh image), so it is
    NOT itself cooldown-blocked — but it DOES stamp the photo field, so a rapid
    follow-up UPLOAD is still throttled by D-08.
    """
    from app.services.telegram import telegram_service, SessionAuthError

    sender = await _load_sender_by_slug(db, ctx, slug)

    try:
        res = await telegram_service.delete_profile_photos(
            sender.slug, sender.session_string, proxy=sender.proxy
        )
    except SessionAuthError as e:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "AUTH_ERROR",
                "message": f"Session auth failed: {e.auth_status}",
                "auth_status": e.auth_status,
            },
        )
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — mapped to a structured HTTP error (incl. FLOOD_WAIT)
        _raise_profile_telegram_error(e)

    sender.tg_photo = None
    sender.tg_photo_mime = None
    _stamp_profile_change(sender, "photo")
    await db.commit()
    await db.refresh(sender)
    return ProfileUpdateResponse(
        sender=_sender_to_response(sender), warnings=_profile_advisory(sender)
    )


@router.get("/senders/{slug}/photo")
async def serve_sender_photo(
    slug: str,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """PROF-07 / D-11: serve the cached profile photo bytes through an AUTH-GATED
    endpoint — never a raw blob URL, never base64-inlined into the list. 404 when
    no photo is cached; a foreign-workspace slug is an opaque 404 (workspace scope).

    The distinct `/photo` suffix keeps this from colliding with GET /senders/{slug}.
    """
    sender = await _load_sender_by_slug(db, ctx, slug)
    if not sender.tg_photo:
        raise HTTPException(
            status_code=404,
            detail={"code": "NO_PHOTO", "message": "No cached photo"},
        )
    return Response(content=sender.tg_photo, media_type=sender.tg_photo_mime or "image/jpeg")


# ─── Account profile resync (Phase 20 — PROF-06, D-12) ───────────────────────


@router.post("/senders/{slug}/resync", response_model=SenderResponse)
async def resync_sender_profile(
    slug: str,
    ctx: AuthCtx = Depends(auth_dep),
    db: AsyncSession = Depends(get_db),
):
    """PROF-06 / D-12: pull the live username / bio / photo from Telegram into the
    cache. This is a READ-from-Telegram (does not open the edit form / does not
    mutate the account), so it carries NO cooldown and NO per-field stamp.
    """
    from app.services.telegram import telegram_service, SessionAuthError

    sender = await _load_sender_by_slug(db, ctx, slug)

    try:
        res = await telegram_service.fetch_profile(
            sender.slug, sender.session_string, proxy=sender.proxy
        )
    except SessionAuthError as e:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "AUTH_ERROR",
                "message": f"Session auth failed: {e.auth_status}",
                "auth_status": e.auth_status,
            },
        )
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — mapped to a structured HTTP error (incl. FLOOD_WAIT)
        _raise_profile_telegram_error(e)

    res = res or {}
    sender.tg_username = res.get("username")
    sender.tg_bio = res.get("bio")
    photo = res.get("photo")
    if photo is not None:
        sender.tg_photo = photo
        sender.tg_photo_mime = res.get("photo_mime") or "image/jpeg"
    elif res.get("has_photo") is False:
        # Live account has no photo → clear the cache.
        sender.tg_photo = None
        sender.tg_photo_mime = None
    await db.commit()
    await db.refresh(sender)

    resp = _sender_to_response(sender)
    # Honour the service's authoritative has_photo when it reports one without
    # shipping the raw bytes (e.g. a lightweight resync); in prod the derived
    # bool(tg_photo) already matches.
    if res.get("has_photo") is not None:
        resp.has_photo = bool(res.get("has_photo"))
    return resp


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
